import os
import gspread
import openai
import requests
import threading
import json
import re
from google.oauth2.service_account import Credentials
from io import BytesIO
import random
from flask import Flask, request, redirect, url_for, flash

# 🔑 API Keys & Config
OPENAI_API_KEY = "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"
SHOPIFY_API_KEY = "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"
SHOPIFY_STORE_URL = "92c6ce-58.myshopify.com" 
SHEET_NAME = "Pinterest_Automation"
CREDENTIALS_FILE = "google_sheets_credentials.json"

# Load the JSON data
with open(CREDENTIALS_FILE, "r") as f:
    credentials_data = json.load(f)

# Define the required scopes
scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

# Authenticate using the service account key
creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scope)
client = gspread.authorize(creds)

# Open the Google Sheet
SHEET_ID = "1SutOYJ0UA-DDy1d4Xf86jf4wh8-ImNG5Tq0lcvMlqmE"


print(creds)

try: 
    sheet = client.open_by_key(SHEET_ID).sheet1  # Open by ID
    print("✅ Connected to Google Sheets successfully!")
except Exception as e:
    print(f"❌ Google Sheets Error: {e}")
    exit(1)  # Stop script if Google Sheets connection fails


# 🔥 Flask App Setup
app = Flask(__name__)
app.secret_key = "f9edeffde607fa5e621291b3663602326ad474ccdb4cdcfefbd31262574bada0"  # Change this to a strong secret key

# New root route
@app.route('/')
def index():
    return 'Welcome to the home page!'


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
            for col in response.json().get(f"{key}_collections", []):
                collections[str(col["id"])] = col["title"]

    return collections

# ✅ Fetch Reviews or Generate Social Proof
def fetch_product_reviews(product_id):
    """Fetches reviews from Shopify or generates a default social proof message."""
    reviews_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/products/{product_id}.json"
    headers = {"X-Shopify-Access-Token": SHOPIFY_API_KEY}

    response = requests.get(reviews_url, headers=headers)
    if response.status_code == 200:
        product_data = response.json().get("product", {})
        review_count = product_data.get("metafields", {}).get("reviews_count", 0)
        average_rating = product_data.get("metafields", {}).get("average_rating", "N/A")

        if review_count and average_rating != "N/A":
            return f"⭐ {average_rating}/5 Sterne von {review_count}+ Kunden!"

    # ✅ Fallback message if no reviews exist
    return "🔥 Bestseller – Trendet auf TikTok! 5000+ zufriedene Kunden!"


# ✅ Fetch Active Products
def fetch_product_data(collection_id, image_limit=3):
    print(f"🔄 Fetching products for collection {collection_id}...")
    url = f"https://{SHOPIFY_STORE_URL}/admin/api/2023-04/collections/{collection_id}/products.json"
    headers = {"X-Shopify-Access-Token": SHOPIFY_API_KEY}
    image_data = []
    since_id = 0
    collections = fetch_collections()
    collection_name = collections.get(collection_id, "General Fashion")

    def process_product(product):
        """Extracts image data from a single product"""
        product_name = product.get("title", "Unknown Product").replace("_", " ")
        product_url = f"https://{SHOPIFY_STORE_URL}/products/{product.get('handle', 'unknown')}"
        product_price = product.get("variants", [{}])[0].get("price", "N/A")
        product_type = product.get("product_type", "N/A")

        if product.get("status") != "active":
            print(f"⏭️ Skipping draft product: {product_name}")
            return

        tags = product.get("tags", "")
        tags = ", ".join(tags) if isinstance(tags, list) else tags.strip()
        product_id = product.get("id", "N/A")
        review_summary = fetch_product_reviews(product_id)

        images = product.get("images", [])[:image_limit]
        for image in images:
            image_data.append((
                image["src"], product_name, product_url, product_price,
                product_type, collection_name, tags, review_summary
            ))

    while True:
        params = {"limit": 250, "since_id": since_id}
        response = requests.get(url, headers=headers, params=params)

        if response.status_code != 200:
            print(f"❌ Error fetching products: {response.status_code}")
            break

        products = response.json().get("products", [])
        if not products:
            break

        threads = []
        for product in products:
            thread = threading.Thread(target=process_product, args=(product,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        since_id = products[-1]["id"]

    return image_data

# ✅ AI-Generated Pin Text
def generate_ai_pin_text(product_name, product_price, tags, product_type, collection_name):
    clean_product_name = product_name.replace("_", " ")
    prompt = f"""
Erstelle eine Pinterest-Pin-Beschreibung für ein E-Commerce-Produkt.

**Produktdetails:**
- 📌 Name: {clean_product_name}
- 💰 Preis: {product_price} €
- 🏷 Tags: {tags}
- 🛍 Kategorie: {product_type}

**Antwortformat:**
1️⃣ **Pin-Titel** (max. 50 Zeichen)  
2️⃣ **Pin-Beschreibung** (max. 200 Zeichen)  
3️⃣ **SEO-Hashtags** (3-5 relevante Hashtags)  
"""

    try:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": prompt}]
        )

        ai_text = response.choices[0].message.content.strip().split("\n")
        if len(ai_text) < 3:
            print(f"⚠️ AI response incomplete: {ai_text}")
            return f"{clean_product_name} – Jetzt entdecken!", "Ein Must-Have für 2025!", "#Mode #Trend #Neu"

        return ai_text[0].strip(), ai_text[1].strip(), ai_text[2].strip()

    except Exception as e:
        print(f"❌ AI Error: {e}")
        return f"{clean_product_name} – Jetzt entdecken!", "Ein Must-Have für 2025!", "#Mode #Trend #Neu"

# ✅ Save Data to Google Sheets
def save_to_google_sheets(image_data):
    headers = [
        "Image URL", "Product Name", "Product URL", "Product Price",
        "Product Type", "Collection Name", "Tags", "Review Summary",
        "Generated Pin Title", "Generated Pin Description", "Board Title"
    ]

    existing_records = sheet.get_all_values()
    if not existing_records:
        sheet.insert_row(headers, 1)

    rows_to_add = []
    for data in image_data:
        if len(data) < 8:
            print(f"⚠️ Skipping entry due to missing data: {data}")
            continue

        pin_title, pin_description, board_title = generate_ai_pin_text(*data[:4], data[5])
        full_data = list(data) + [pin_title, pin_description, board_title]
        rows_to_add.append(full_data)

    if rows_to_add:
        sheet.append_rows(rows_to_add)

    print(f"✅ {len(rows_to_add)} new Pins added to Google Sheets.")

# ✅ Run Everything
def run_pinterest_automation(collection_id, image_limit=3):
    print("🔄 Fetching Shopify Data...")
    image_data = fetch_product_data(collection_id, image_limit)

    print("📤 Saving to Google Sheets...")
    save_to_google_sheets(image_data)

    print("✅ Done! Data is ready for automation.")

# ✅ Flask Routes
@app.route("/process", methods=["POST"])
def process_collection( ):
    collection_id = request.form.get("collection_id")
    threading.Thread(target=run_pinterest_automation, args=(collection_id,)).start()
    flash(f"✅ Die Verarbeitung für {collection_id} wurde gestartet!", "success")
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)