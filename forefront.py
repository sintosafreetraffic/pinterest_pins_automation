import os
import shutil
import string
import threading
import requests
import queue
import gspread
import openai
import re
import time
import sys
import zipfile
import random
import concurrent.futures
from collections import defaultdict
from google.oauth2.service_account import Credentials
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory
from script import run_pinterest_automation  # Import main function from script.py

# 🔑 Shopify API Credentials (Ensure they are also set in script.py)
SHOPIFY_API_KEY = "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"
SHOPIFY_STORE_URL = "92c6ce-58.myshopify.com"
CREDENTIALS_FILE = "google_sheets_credentials.json"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ✅ Authenticate Google Sheets
scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scope)
client = gspread.authorize(creds)

# ✅ Connect to Google Sheets
SHEET_ID = "1NuxtCo3z1DKHk7GLW-uIqHs6kFpz3G3gF_tKInciBEE"
try:
    sheet = client.open_by_key(SHEET_ID).sheet1
    print("✅ Connected to Google Sheets successfully!")
except Exception as e:
    print(f"❌ Google Sheets Error: {e}")
    exit(1)

# 🔥 Flask Setup (Corrected)
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.urandom(24)
app.config["UPLOAD_FOLDER"] = "static/uploads"
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True  # ✅ Ensures Jinja updates on file change

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)  # ✅ Ensure upload directory exists

# ✅ Upload Logo Route
@app.route("/upload_logo", methods=["POST"])
def upload_logo():
    if "file" not in request.files:
        flash("⚠️ No file selected!", "error")
        return redirect(url_for("index"))

    file = request.files["file"]
    if file.filename == "":
        flash("⚠️ No selected file!", "error")
        return redirect(url_for("index"))

    file_path = os.path.join(app.config["UPLOAD_FOLDER"], "logo.png")
    file.save(file_path)
    flash("✅ Logo successfully uploaded!", "success")
    return redirect(url_for("index"))

# ✅ Fetch Shopify Collections
def fetch_collections():
    headers = {"X-Shopify-Access-Token": SHOPIFY_API_KEY}
    collections = {}

    urls = {
        "smart": f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/smart_collections.json",
        "custom": f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/custom_collections.json"
    }

    for key, url in urls.items():
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            for col in response.json().get(key + "_collections", []):
                collections[str(col["id"])] = col["title"]

    return collections

# ✅ Fetch Shopify Products (Parallel Processing)
def fetch_product_data(collection_id, image_limit=3):
    print(f"🔄 Fetching products for collection {collection_id}...")
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/collections/{collection_id}/products.json"
    headers = {"X-Shopify-Access-Token": SHOPIFY_API_KEY}
    image_data = []
    collections = fetch_collections()
    collection_name = collections.get(collection_id, "General Fashion")

    def process_product(product):
        """Extracts image data from a single product"""
        product_name = product.get("title", "Unknown Product").replace("_", " ")
        product_url = f"https://{SHOPIFY_STORE_URL}/products/{product.get('handle', 'unknown')}"
        product_price = product.get("variants", [{}])[0].get("price", "N/A")
        product_type = product.get("product_type", "N/A")

        if product.get("status") != "active":
            return

        tags = ", ".join(product.get("tags", [])) if isinstance(product.get("tags", []), list) else product.get("tags", "").strip()
        review_summary = fetch_product_reviews(product.get("id", "N/A"))

        images = product.get("images", [])[:image_limit]
        for image in images:
            image_data.append((
                image["src"], product_name, product_url, product_price,
                product_type, collection_name, tags, review_summary
            ))

    params = {"limit": 250}
    response = requests.get(url, headers=headers, params=params)

    if response.status_code != 200:
        print(f"❌ Error fetching products: {response.status_code}")
        return []

    products = response.json().get("products", [])

    threads = []
    for product in products:
        thread = threading.Thread(target=process_product, args=(product,))
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    return image_data

# ✅ Fetch Product Reviews
def fetch_product_reviews(product_id):
    reviews_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/products/{product_id}.json"
    headers = {"X-Shopify-Access-Token": SHOPIFY_API_KEY}
    response = requests.get(reviews_url, headers=headers)

    if response.status_code == 200:
        product_data = response.json().get("product", {})
        review_count = product_data.get("metafields", {}).get("reviews_count", 0)
        avg_rating = product_data.get("metafields", {}).get("average_rating", "N/A")

        if review_count and avg_rating != "N/A":
            return f"⭐ {avg_rating}/5 Sterne von {review_count}+ Kunden!"

    return "🔥 Bestseller – Trendet auf TikTok!"

# ✅ Define a retry decorator to handle rate limits
def retry_on_rate_limit(func):
    def wrapper(*args, **kwargs):
        retries = 5  # Maximum retries
        wait_time = 3  # Initial wait time
        for attempt in range(retries):
            try:
                return func(*args, **kwargs)
            except openai.OpenAIError as e:
                if "rate_limit_exceeded" in str(e):
                    print(f"❌ AI Error (Retry {attempt+1}/{retries}): {e}")
                    time.sleep(wait_time)
                    wait_time *= 2  # Exponential backoff
                else:
                    raise e
        return "Rate Limit Error"
    return wrapper

@retry_on_rate_limit
def generate_single_pin_title(data):
    """Generate a Pinterest Pin Title using GPT-4o Mini."""
    product_name, product_price, _, _, product_type, collection_name, tags, _ = data  
    clean_product_name = product_name.replace("_", " ")

    prompt = f"""
    Schreibe einen kurzen, klickstarken Pinterest-Pin-Titel für ein Produkt.

    **Produktdetails:**
    - 📌 Name: {clean_product_name}
    - 💰 Preis: {product_price} €
    - 🏷 Tags: {tags}
    - 🛍 Kategorie: {product_type}

    **Antwortformat:**
    Pin-Titel  
    """

    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-4o-mini",  # ✅ Use Mini for speed
        messages=[{"role": "system", "content": prompt}],
        max_tokens=30
    )

    pin_title = response.choices[0].message.content.strip()
    return re.sub(r"^\d️⃣\*\*|\*\*", "", pin_title).strip() or f"{clean_product_name} – Jetzt entdecken!"

@retry_on_rate_limit
def generate_single_pin_description(data):
    """Generate a Pinterest Pin Description with Hashtags using GPT-4o."""
    product_name, product_price, _, _, product_type, collection_name, tags, _ = data  
    clean_product_name = product_name.replace("_", " ")

    prompt = f"""
    Schreibe eine SEO-optimierte Pinterest-Pin-Beschreibung für ein Produkt (max. 200 Zeichen).  
    Am Ende sollten 2-3 relevante Hashtags enthalten sein.

    **Produktdetails:**
    - 📌 Name: {clean_product_name}
    - 💰 Preis: {product_price} €
    - 🏷 Tags: {tags}
    - 🛍 Kategorie: {product_type}

    **Antwortformat:**
    Pin-Beschreibung  
    """

    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-4o-mini",  
        messages=[{"role": "system", "content": prompt}],
        max_tokens=100
    )

    pin_description = response.choices[0].message.content.strip()
    return re.sub(r"^\d️⃣\*\*|\*\*", "", pin_description).strip() or "Ein Must-Have für 2025! #Trend #Fashion"

@retry_on_rate_limit
def generate_board_title_for_collection(collection_name, board_titles_cache):
    """Generate a Pinterest Board Title for a collection using GPT-4o Mini."""
    if collection_name in board_titles_cache:
        return board_titles_cache[collection_name]

    prompt = f"""
    Erstelle einen kurzen, thematisch passenden Titel für ein Pinterest-Board auf Deutsch, in das diese Kollektion passt.

    **Kollektion:** {collection_name}

    **Antwortformat:**
    Pin-Board-Titel  
    """

    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-4o-mini",  # ✅ Use Mini for fast Board Titles
        messages=[{"role": "system", "content": prompt}],
        max_tokens=30
    )

    pin_board_title = response.choices[0].message.content.strip()
    board_titles_cache[collection_name] = re.sub(r"^\d️⃣\*\*|\*\*", "", pin_board_title).strip() or "Trend-Produkte"
    return board_titles_cache[collection_name]

def update_progress(completed, total):
    """Live progress update in the console."""
    sys.stdout.write(f"\r✅ {completed}/{total} Pins generated...")
    sys.stdout.flush()


def generate_ai_pin_text_batch(image_data):
    """Generates AI-optimized Pin title, description & board title for multiple products with progress tracking."""
    total_pins = len(image_data)  # ✅ Total pins to generate
    completed = 0  # ✅ Counter to track progress

    print(f"🚀 Generating {total_pins} AI Pin Texts...")

    board_titles_cache = {}  # ✅ Store one Board Title per collection

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        pin_titles = []
        pin_descriptions = []

        future_to_product = {executor.submit(generate_single_pin_title, data): data for data in image_data}
        for future in concurrent.futures.as_completed(future_to_product):
            pin_titles.append(future.result())
            completed += 1
            update_progress(completed, total_pins)  # ✅ Show progress

        future_to_product = {executor.submit(generate_single_pin_description, data): data for data in image_data}
        for future in concurrent.futures.as_completed(future_to_product):
            pin_descriptions.append(future.result())
            completed += 1
            update_progress(completed, total_pins)  # ✅ Show progress

    pin_board_titles = [generate_board_title_for_collection(data[5], board_titles_cache) for data in image_data]

    print(f"\n✅ Completed! Generated {len(image_data)} Pins successfully.")
    
    return list(zip(pin_titles, pin_descriptions, pin_board_titles))

# ✅ Save to Google Sheets (Aligned with AI Output)
def save_to_google_sheets(image_data):
    headers = [
        "Image URL", "Product Name", "Product URL", "Product Price",
        "Product Type", "Collection Name", "Tags", "Review Summary",
        "Generated Pin Title", "Generated Pin Description", "Board Title"
    ]

    existing_records = sheet.get_all_values()  # 🔥 Ensure this is defined before using it

    # ✅ Ensure headers are always in the first row
    if not existing_records or existing_records[0] != headers:
        print("⚠️ Headers missing or incorrect. Adding headers now...")
        sheet.insert_row(headers, 1)

    print(f"🔍 DEBUG: Image Data Length: {len(image_data)}")

    # ✅ Ensure `ai_results` is not None
    ai_results = generate_ai_pin_text_batch(image_data) or []

    print(f"🔍 DEBUG: AI Batch Output Length: {len(ai_results)}")
    print(f"🔍 Sample AI Result: {ai_results[:3]}") 

    if not isinstance(ai_results, list):  # 🔥 Double-check that it's a list
        print("❌ Error: AI generation failed, using fallback values.")
        ai_results = [["Fehlendes Pin-Titel", "Fehlende Beschreibung", "Trend-Produkte"]] * len(image_data)

    rows_to_add = []
    for i, data in enumerate(image_data):
        if i < len(ai_results):  # Ensure we don't access an invalid index
            pin_title, pin_description, board_title = ai_results[i]  # Unpack AI-generated values
            full_data = list(data) + [pin_title, pin_description, board_title]  # Append AI output correctly
            rows_to_add.append(full_data)

    if rows_to_add:
        sheet.append_rows(rows_to_add)  # ✅ Append all rows at once for speed

    print(f"✅ {len(rows_to_add)} new Pins added to Google Sheets.")


# ✅ Start Process
def run_pinterest_automation(collection_id, image_limit=10):
    print("🔄 Fetching Shopify Data...")
    image_data = fetch_product_data(collection_id, image_limit)

    print("📤 Saving to Google Sheets...")
    save_to_google_sheets(image_data)

    print("✅ Done! Data is ready for automation.")

# ✅ Flask Routes
@app.route("/process", methods=["POST"])
def process_collection():
    collection_id = request.form.get("collection_id")
    threading.Thread(target=run_pinterest_automation, args=(collection_id,)).start()
    flash(f"✅ Die Verarbeitung für {collection_id} wurde gestartet!", "success")
    return redirect(url_for("index"))

@app.route("/")
def index():
    collections_dict = fetch_collections()

    print("\n🔍 DEBUG STEP 1: Collections fetched from Shopify API:")
    print(collections_dict)  # ✅ Print raw collections

    collections = list(collections_dict.items())  # Convert to list
    
    print("\n🔍 DEBUG STEP 2: Formatted collections list:")
    print(collections)  # ✅ Print formatted list

    if not collections:
        print("\n❌ ERROR: No collections found! Check your fetch_collections() function.")

    return render_template("index.html", collections=collections)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)