import os
import time
import mimetypes
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import streamlit as st
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Page configuration
st.set_page_config(page_title="Reverb Cloner PRO", page_icon="🎸", layout="centered")
st.title("🎸 Reverb Cloner PRO MAX - FINAL VERSION")
st.markdown("---")

API_BASE = "https://api.reverb.com/api"
IMAGE_DIR = Path("images")


def create_session():
    """Create resilient HTTP session with retries."""
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.7,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "PUT"],
    )
    adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def extract_listing_id(url):
    """Extract listing ID from Reverb URL."""
    try:
        if "/item/" in url:
            part = url.split("/item/")[1]
            return part.split("-")[0]
        if "reverb.com/item/" in url:
            part = url.split("reverb.com/item/")[1]
            return part.split("-")[0]
        return None
    except Exception as e:
        st.error(f"Error parsing URL: {e}")
        return None


def get_listing(session, api_key, listing_id):
    """Fetch original listing data."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept-Version": "3.0",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        response = session.get(
            f"{API_BASE}/listings/{listing_id}",
            headers=headers,
            timeout=15,
        )

        if response.status_code != 200:
            st.error(f"Error fetching listing: {response.status_code} - {response.text}")
            return None

        return response.json()
    except Exception as e:
        st.error(f"Connection error: {e}")
        return None


def extract_make_model(listing):
    """Extract make and model correctly from listing."""
    make = listing.get("make")
    make_name = "Unknown"

    if make:
        if isinstance(make, dict):
            make_name = make.get("name", "Unknown")
            if not make_name or make_name == "Unknown":
                make_name = str(make.get("_id", "Unknown"))
        elif isinstance(make, str):
            make_name = make
        elif isinstance(make, (int, float)):
            make_name = str(make)

    model = listing.get("model")
    model_name = "Unknown"

    if model:
        if isinstance(model, dict):
            model_name = model.get("name", "Unknown")
            if not model_name or model_name == "Unknown":
                model_name = str(model.get("_id", "Unknown"))
        elif isinstance(model, str):
            model_name = model
        elif isinstance(model, (int, float)):
            model_name = str(model)

    return make_name, model_name


def _resolve_image_url(photo):
    if "_links" in photo:
        links = photo["_links"]
        for key in ["full", "download", "original", "large", "self"]:
            if key in links and isinstance(links[key], dict):
                href = links[key].get("href")
                if href:
                    return href
    if "href" in photo:
        return photo["href"]
    for value in photo.values():
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return None


def _download_one_image(session, image_url, image_index, listing_id):
    response = session.get(image_url, timeout=20)
    response.raise_for_status()

    ext = Path(image_url.split("?")[0]).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        ext = ".jpg"

    file_path = IMAGE_DIR / f"listing_{listing_id}_img_{image_index}{ext}"
    file_path.write_bytes(response.content)

    if file_path.stat().st_size == 0:
        raise ValueError("Downloaded image is empty")

    return str(file_path)


def download_images(session, listing, listing_id, max_workers=8):
    """Download images concurrently for speed."""
    photos = listing.get("photos", [])

    if not photos:
        st.warning("No images found in this listing")
        return []

    IMAGE_DIR.mkdir(exist_ok=True)

    tasks = []
    for i, photo in enumerate(photos):
        image_url = _resolve_image_url(photo)
        if image_url:
            tasks.append((i, image_url))

    if not tasks:
        st.warning("Could not detect downloadable image URLs.")
        return []

    progress_bar = st.progress(0)
    status_text = st.empty()
    paths = []

    with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as executor:
        future_map = {
            executor.submit(_download_one_image, session, url, index, listing_id): index
            for index, url in tasks
        }
        completed = 0

        for future in as_completed(future_map):
            index = future_map[future]
            completed += 1
            status_text.text(f"Downloading images... ({completed}/{len(tasks)})")
            try:
                paths.append(future.result())
            except Exception as e:
                st.warning(f"Error downloading image {index + 1}: {e}")
            progress_bar.progress(completed / len(tasks))

    progress_bar.empty()
    status_text.text(f"Download complete: {len(paths)}/{len(tasks)} images")

    return sorted(paths)


def create_listing(session, api_key, original_listing, shipping_profile_id, price_multiplier):
    """Create new listing based on original and return {id, listing_data}."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept-Version": "3.0",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    make_name, model_name = extract_make_model(original_listing)

    original_price = float(original_listing["price"]["amount"])
    new_price = round(original_price * price_multiplier, 2)
    new_price_cents = int(new_price * 100)

    condition_uuid = None
    condition = original_listing.get("condition")
    if condition:
        if isinstance(condition, dict):
            condition_uuid = condition.get("uuid")
        elif isinstance(condition, str):
            condition_uuid = condition

    if not condition_uuid:
        condition_uuid = "df268ad1-c462-4ba6-b6db-e007e23922ea"

    description = original_listing.get("description", "")
    if not description:
        description = f"Original listing: {original_listing.get('title', 'No title')}"

    title = original_listing.get("title", f"{make_name} {model_name}".strip())
    if not title or title == "Unknown":
        title = f"{make_name} {model_name}".strip()

    finish = original_listing.get("finish", "")
    year = original_listing.get("year", "")

    categories = original_listing.get("categories", [])
    category_uuids = [cat["uuid"] for cat in categories if isinstance(cat, dict) and "uuid" in cat]

    payload = {
        "title": title,
        "description": description,
        "price": {
            "amount": new_price,
            "amount_cents": new_price_cents,
            "currency": original_listing["price"]["currency"],
        },
        "condition": {"uuid": condition_uuid},
        "make": make_name,
        "model": model_name,
        "finish": finish,
        "year": year,
        "shipping_profile_id": int(shipping_profile_id),
        "state": "draft",
    }

    if category_uuids:
        payload["category_uuids"] = category_uuids

    try:
        response = session.post(
            f"{API_BASE}/listings",
            headers=headers,
            json=payload,
            timeout=30,
        )

        if response.status_code not in [200, 201]:
            st.error(f"Error creating listing: {response.status_code}")
            st.error(f"Response: {response.text}")
            return None

        data = response.json()

        if isinstance(data, dict):
            if "listing" in data and isinstance(data["listing"], dict):
                created = data["listing"]
                return {"id": created.get("id"), "listing_data": created}
            if "id" in data:
                return {"id": data["id"], "listing_data": data}

        return None

    except Exception as e:
        st.error(f"Connection error: {e}")
        return None


def _listing_read_endpoints(listing_id):
    return [
        f"{API_BASE}/my/listings/{listing_id}",
        f"{API_BASE}/listings/{listing_id}",
    ]


def _listing_image_endpoints(listing_id):
    return [
        f"{API_BASE}/my/listings/{listing_id}/images",
        f"{API_BASE}/my/listings/{listing_id}/photos",
        f"{API_BASE}/listings/{listing_id}/images",
        f"{API_BASE}/listings/{listing_id}/photos",
    ]


def _extract_href(value):
    if isinstance(value, dict):
        href = value.get("href")
        if isinstance(href, str):
            return href
    if isinstance(value, str):
        return value
    return None


def _extract_upload_endpoints_from_listing_data(listing_data):
    if not isinstance(listing_data, dict):
        return []

    links = listing_data.get("_links")
    if not isinstance(links, dict):
        return []

    candidates = []
    for key in ["images", "photos", "add_photo", "add_image", "upload_photo", "upload_image"]:
        if key in links:
            href = _extract_href(links[key])
            if href:
                candidates.append(href)

    unique = []
    seen = set()
    for endpoint in candidates:
        if endpoint not in seen:
            seen.add(endpoint)
            unique.append(endpoint)
    return unique


def _get_listing_data(session, api_key, listing_id):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept-Version": "3.0",
    }
    for endpoint in _listing_read_endpoints(listing_id):
        try:
            response = session.get(endpoint, headers=headers, timeout=12)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, dict):
                    return data
        except Exception:
            continue
    return None


def wait_until_listing_ready(session, api_key, listing_id, max_wait_seconds=35):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept-Version": "3.0",
    }
    start = time.time()
    while time.time() - start < max_wait_seconds:
        for endpoint in _listing_read_endpoints(listing_id):
            try:
                check_response = session.get(endpoint, headers=headers, timeout=10)
                if check_response.status_code == 200:
                    return True
            except Exception:
                pass
        time.sleep(2)
    return False


def get_photo_count(session, api_key, listing_id):
    data = _get_listing_data(session, api_key, listing_id)
    if not isinstance(data, dict):
        return 0
    photos = data.get("photos") or data.get("images") or []
    if isinstance(photos, list):
        return len(photos)
    return 0


def upload_images(session, api_key, listing_id, image_paths, created_listing_data=None):
    """Upload images with endpoint fallback and robust multipart field handling."""
    if not image_paths:
        st.warning("No images to upload")
        return False

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept-Version": "3.0",
    }

    if not wait_until_listing_ready(session, api_key, listing_id):
        st.error("❌ Listing is not ready for image upload yet.")
        return False

    progress_bar = st.progress(0)
    status_text = st.empty()
    successful_uploads = 0

    discovered_endpoints = _extract_upload_endpoints_from_listing_data(created_listing_data)
    listing_data = _get_listing_data(session, api_key, listing_id)
    discovered_endpoints.extend(_extract_upload_endpoints_from_listing_data(listing_data))
    fallback_endpoints = _listing_image_endpoints(listing_id)
    endpoints = []
    seen = set()
    for endpoint in discovered_endpoints + fallback_endpoints:
        if endpoint and endpoint not in seen:
            seen.add(endpoint)
            endpoints.append(endpoint)
    field_names = ["photo", "image", "file"]

    for i, image_path in enumerate(image_paths):
        status_text.text(f"Uploading image {i + 1} of {len(image_paths)}")
        try:
            if not os.path.exists(image_path) or os.path.getsize(image_path) == 0:
                st.warning(f"⚠️ Invalid image file: {image_path}")
                progress_bar.progress((i + 1) / len(image_paths))
                continue

            mime_type, _ = mimetypes.guess_type(image_path)
            if not mime_type:
                mime_type = "image/jpeg"

            uploaded = False
            last_error = ""

            for endpoint in endpoints:
                if uploaded:
                    break
                for field_name in field_names:
                    if uploaded:
                        break

                    for attempt in range(1, 4):
                        with open(image_path, "rb") as img_file:
                            files = {
                                field_name: (Path(image_path).name, img_file, mime_type),
                            }
                            response = session.post(endpoint, headers=headers, files=files, timeout=45)

                        if response.status_code in [200, 201, 202, 204]:
                            successful_uploads += 1
                            uploaded = True
                            st.write(f"✅ Uploaded image {i + 1} via {endpoint} ({field_name})")
                            break

                        body_preview = (response.text or "")[:180].replace("\n", " ")
                        last_error = f"{response.status_code} from {endpoint} ({field_name}) {body_preview}"

                        if response.status_code in [404, 405]:
                            break
                        if response.status_code in [429, 500, 502, 503, 504]:
                            time.sleep(1.5 * attempt)
                            continue
                        break

            if not uploaded:
                st.warning(f"❌ Failed to upload image {i + 1}. Last error: {last_error or 'unknown'}")

        except Exception as e:
            st.warning(f"❌ Error uploading image {i + 1}: {e}")

        progress_bar.progress((i + 1) / len(image_paths))

    status_text.text(f"Upload complete! {successful_uploads}/{len(image_paths)} images uploaded")
    progress_bar.empty()

    if successful_uploads == 0:
        remote_photo_count = get_photo_count(session, api_key, listing_id)
        if remote_photo_count > 0:
            st.success(f"✅ Listing already has {remote_photo_count} images on Reverb.")
            return True

        st.warning("⚠️ Could not upload images via API.")
        st.info("📌 **Manual Upload Option:**")
        st.markdown("1. Your images are saved in the **'images'** folder")
        st.markdown(f"2. Go to your draft listing: [Edit Listing](https://reverb.com/item/{listing_id}/edit)")
        st.markdown("3. Upload the images manually through the Reverb website")
        st.markdown("4. Then publish the listing")

    return successful_uploads > 0


def publish_listing(session, api_key, listing_id):
    """Publish a draft listing."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept-Version": "3.0",
        "Content-Type": "application/json",
    }

    publish_targets = [
        ("post", f"{API_BASE}/my/listings/{listing_id}/publish"),
        ("put", f"{API_BASE}/my/listings/{listing_id}/publish"),
        ("post", f"{API_BASE}/listings/{listing_id}/publish"),
        ("put", f"{API_BASE}/listings/{listing_id}/publish"),
    ]

    for method, endpoint in publish_targets:
        try:
            if method == "post":
                response = session.post(endpoint, headers=headers, timeout=15)
            else:
                response = session.put(endpoint, headers=headers, timeout=15)

            if response.status_code in [200, 201, 202, 204]:
                st.write(f"✅ Listing {listing_id} published successfully")
                return True

            if response.status_code in [404, 405]:
                continue

            st.warning(f"Could not publish listing: {response.status_code}")
            st.info(f"💡 You can publish manually: https://reverb.com/item/{listing_id}/edit")
            return False
        except Exception as e:
            st.warning(f"Error publishing listing on {endpoint}: {e}")

    st.warning("Could not publish listing automatically with available endpoints.")
    st.info(f"💡 You can publish manually: https://reverb.com/item/{listing_id}/edit")
    return False


def cleanup_images(image_paths, keep_images=False):
    """Clean up downloaded images."""
    if keep_images:
        return

    for image_path in image_paths:
        try:
            if os.path.exists(image_path):
                os.remove(image_path)
        except Exception:
            pass


def normalize_listing_urls(listing_urls_raw):
    lines = [line.strip() for line in listing_urls_raw.splitlines() if line.strip()]
    unique_lines = []
    seen = set()
    for line in lines:
        if line not in seen:
            seen.add(line)
            unique_lines.append(line)
    return unique_lines


# ===== Streamlit UI =====
with st.sidebar:
    st.header("⚙️ Settings")

    price_multiplier = st.slider(
        "Price Multiplier",
        min_value=0.1,
        max_value=2.0,
        value=0.7,
        step=0.05,
        help="Multiply original price by this value",
    )

    keep_images = st.checkbox(
        "Keep images after upload",
        value=False,
        help="Keep downloaded images locally after upload",
    )

    auto_publish = st.checkbox(
        "Auto-publish listing",
        value=True,
        help="Automatically publish the listing after image upload",
    )

    max_download_workers = st.slider(
        "Image download concurrency",
        min_value=1,
        max_value=16,
        value=8,
        step=1,
        help="Higher values speed up photo downloading for listings with many images.",
    )

    st.markdown("---")
    st.markdown("### 📌 Note")
    st.markdown("Paste one listing URL per line to clone many listings (10+ supported).")


api_key = st.text_input("🔑 API Key", type="password", help="Enter your Reverb API key")
shipping_profile_id = st.text_input("📦 Shipping Profile ID", help="Enter your Shipping Profile ID")
listing_urls_raw = st.text_area(
    "🔗 Listing URLs (one per line)",
    height=180,
    help="You can paste 1, 10, or more URLs. The app will process them in order.",
)

if st.button("🚀 Start Cloning", type="primary", use_container_width=True):
    if not api_key:
        st.error("❌ Please enter your API Key")
        st.stop()

    if not shipping_profile_id:
        st.error("❌ Please enter your Shipping Profile ID")
        st.stop()

    listing_urls = normalize_listing_urls(listing_urls_raw)
    if not listing_urls:
        st.error("❌ Please enter at least one Listing URL")
        st.stop()

    st.info(f"📦 Total URLs queued: {len(listing_urls)}")
    session = create_session()

    summary = []

    for idx, listing_url in enumerate(listing_urls, start=1):
        st.markdown("---")
        st.subheader(f"Processing {idx}/{len(listing_urls)}")
        st.write(f"Source URL: {listing_url}")

        listing_id = extract_listing_id(listing_url)
        if not listing_id:
            st.error("❌ Invalid URL format")
            summary.append((listing_url, None, "invalid_url"))
            continue

        st.info(f"📋 Original Listing ID: {listing_id}")
        original_listing = get_listing(session, api_key, listing_id)
        if not original_listing:
            summary.append((listing_url, None, "fetch_failed"))
            continue

        st.info("📥 Downloading images...")
        image_paths = download_images(session, original_listing, listing_id, max_workers=max_download_workers)
        st.success(f"✅ Downloaded {len(image_paths)} images")

        st.info("📝 Creating new listing...")
        created_listing = create_listing(session, api_key, original_listing, shipping_profile_id, price_multiplier)
        if not created_listing or not created_listing.get("id"):
            cleanup_images(image_paths, keep_images=True)
            summary.append((listing_url, None, "create_failed"))
            continue

        new_listing_id = created_listing["id"]
        st.success(f"✅ Created new listing with ID: {new_listing_id}")

        upload_success = True
        if image_paths:
            st.info("📤 Uploading images...")
            upload_success = upload_images(session, api_key, new_listing_id, image_paths, created_listing_data=created_listing.get("listing_data"))
            if upload_success:
                st.success("✅ Images uploaded successfully")
            else:
                st.warning("⚠️ Some images failed to upload")

        if auto_publish and new_listing_id:
            remote_photo_count = get_photo_count(session, api_key, new_listing_id)
            if upload_success or remote_photo_count > 0:
                st.info("📢 Publishing listing...")
                publish_listing(session, api_key, new_listing_id)
            else:
                st.warning("⚠️ Skip auto-publish: no photos found on listing yet.")
                st.info(f"Add at least one image first: https://reverb.com/item/{new_listing_id}/edit")

        cleanup_images(image_paths, keep_images)
        summary.append((listing_url, new_listing_id, "ok" if upload_success else "upload_partial"))

    st.markdown("---")
    st.subheader("✅ Run Summary")
    success_count = 0
    for source_url, new_id, status in summary:
        if new_id:
            success_count += 1
            st.markdown(f"- **{status}** | Source: `{source_url}` → New listing: https://reverb.com/item/{new_id}")
        else:
            st.markdown(f"- **{status}** | Source: `{source_url}`")

    st.success(f"Completed. Successful clones: {success_count}/{len(summary)}")
    st.balloons()

st.markdown("---")
st.markdown("Made with 🎸 for Reverb sellers | FINAL VERSION with all fixes")
