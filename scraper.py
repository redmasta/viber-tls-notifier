import requests
from bs4 import BeautifulSoup
import json
import os
from viberbot import Api
from viberbot.api.bot_configuration import BotConfiguration
from viberbot.api.messages.text_message import TextMessage
import time
from tenacity import retry, stop_after_attempt, wait_exponential
import datetime
import traceback

# --- Константы и настройки ---
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}
TLS_CONTACT_URL = "https://it.tlscontact.com/by/msq/page.php?pid=news"

# --- Настройка пути для хранения данных на Render ---

DATA_DIR = os.environ.get('RENDER_DISK_MOUNT_PATH', '.')
DATA_FILE = os.path.join(DATA_DIR, "news_cache.json")


# --- Viber bot credentials ---
VIBER_BOT_TOKEN = os.environ.get("VIBER_BOT_TOKEN")
receiver_ids_str = os.environ.get("VIBER_RECEIVER_IDS", "")
VIBER_RECEIVER_IDS = [item.strip() for item in receiver_ids_str.split(',') if item.strip()]

# Viber bot setup
viber = Api(BotConfiguration(
    name="TLS Notifier",
    auth_token=VIBER_BOT_TOKEN,
    avatar="https://example.com/avatar.png"
))


# --- Функции парсинга и отправки (в основном без изменений) ---
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def get_latest_news():
    print(f"Fetching news from: {TLS_CONTACT_URL}")
    news_identifiers = []
    try:
        response = requests.get(TLS_CONTACT_URL, headers=HEADERS, timeout=15)
        response.raise_for_status()
        print(f"TLScontact page fetched successfully. Status: {response.status_code}")
        soup = BeautifulSoup(response.text, "html.parser")
        news_container = soup.select_one("div.card.card-visa")
        if not news_container:
            print("Error: Could not find the main news container 'div.card.card-visa'")
            return []
        title_elements = news_container.select("h3")
        for title_element in title_elements:
            title = title_element.text.strip()
            if not title: continue
            date_str = "Unknown Date"
            title_block_div = title_element.find_parent("div", class_=lambda x: x and 'd-flex' in x.split())
            first_p_after_title = title_block_div.find_next_sibling("p") if title_block_div else None
            if first_p_after_title:
                date_tag = first_p_after_title.select_one("strong > u")
                if date_tag and date_tag.text.strip():
                    raw_date = date_tag.text.strip()
                    try:
                        date_obj = datetime.datetime.strptime(raw_date, "%d/%m/%Y")
                        date_str = date_obj.strftime("%Y-%m-%d")
                    except ValueError:
                        date_str = raw_date
            identifier = f"{date_str} || {title}"
            news_identifiers.append(identifier)
        return news_identifiers
    except requests.exceptions.RequestException as e:
        print(f"Network/HTTP Error fetching news: {e}")
        return []
    except Exception as e:
        print(f"An unexpected error occurred while parsing: {e}")
        traceback.print_exc()
        return []

def load_cached_news():
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, TypeError):
        print(f"Warning: Cache file {DATA_FILE} is corrupted. Starting fresh.")
        return []

def save_news_cache(identifiers):
    try:
        with open(DATA_FILE, "w", encoding='utf-8') as f:
            json.dump(identifiers, f, indent=4, ensure_ascii=False)
        print(f"Saved {len(identifiers)} identifiers to cache file {DATA_FILE}")
    except Exception as e:
        print(f"Error saving cache to {DATA_FILE}: {e}")

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=4, max=10))
def send_viber_message(receiver_id, message):
    try:
        print(f"Sending message to receiver: {receiver_id}")
        viber.send_messages(receiver_id, [TextMessage(text=message)])
        print(f"Successfully sent message to: {receiver_id}")
    except Exception as e:
        print(f"Failed to send Viber message to {receiver_id}: {e}")
        raise e

def main():
    print(f"\n--- Running script at {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
    if not VIBER_RECEIVER_IDS:
        print("CRITICAL: VIBER_RECEIVER_IDS is not set or empty. Exiting.")
        return

    cached_identifiers = load_cached_news()
    is_first_run = not cached_identifiers
    latest_identifiers = get_latest_news()

    if not latest_identifiers:
        print("Could not fetch latest news. Aborting.")
        return

    new_identifiers = [ident for ident in latest_identifiers if ident not in cached_identifiers]
    print(f"\nFound {len(new_identifiers)} new TLS identifiers.")

    if new_identifiers:
        cutoff_date = (datetime.date.today() - datetime.timedelta(days=365)) if is_first_run else None
        if cutoff_date:
            print(f"First run detected. Filtering news older than {cutoff_date}.")

        for identifier in new_identifiers:
            parts = identifier.split(" || ", 1)
            date_part_str, title_part = (parts[0], parts[1]) if len(parts) > 1 else ("Unknown Date", identifier)
            
            send_notification = True
            if is_first_run:
                try:
                    news_date = datetime.datetime.strptime(date_part_str, "%Y-%m-%d").date()
                    if news_date < cutoff_date:
                        send_notification = False
                        print(f"Skipping old news: {identifier}")
                except ValueError:
                    pass # Не можем распарсить дату, отправляем на всякий случай

            if send_notification:
                message = f"🆕 Новая новость TLScontact: {title_part}\nПроверьте: {TLS_CONTACT_URL}"
                print(f"Broadcasting message: \"{message}\"")
                for receiver in VIBER_RECEIVER_IDS:
                    try:
                        send_viber_message(receiver, message)
                        time.sleep(0.2)
                    except Exception as e_send:
                        print(f"Failed to send message to {receiver} after all retries: {e_send}")

        save_news_cache(latest_identifiers)
    else:
        print("No new news from TLScontact.")
    print(f"\n--- Script finished at {time.strftime('%Y-%m-%d %H:%M:%S')} ---")

if __name__ == "__main__":
    main()