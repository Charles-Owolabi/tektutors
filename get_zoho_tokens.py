import os
import requests

def update_env_file(updates):
    env_path = ".env"
    if not os.path.exists(env_path):
        print("Error: .env file not found in the current directory.")
        return False

    try:
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        new_lines = []
        for line in lines:
            line_stripped = line.strip()
            updated = False
            for key, val in updates.items():
                if line_stripped.startswith(f"{key}="):
                    new_lines.append(f"{key}={val}\n")
                    updated = True
                    break
            if not updated:
                new_lines.append(line)

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        return True
    except Exception as e:
        print(f"Error updating .env file: {e}")
        return False

def main():
    print("=== Zoho Meeting Integration Setup Helper ===")
    print("This script will exchange a Zoho OAuth Grant Code for a Refresh Token")
    print("and automatically update your .env file.\n")

    accounts_url = input("Zoho Accounts URL [default: https://accounts.zoho.com]: ").strip() or "https://accounts.zoho.com"
    api_url = input("Zoho Meeting API URL [default: https://meeting.zoho.com]: ").strip() or "https://meeting.zoho.com"

    client_id = input("Enter your Zoho Client ID: ").strip()
    client_secret = input("Enter your Zoho Client Secret: ").strip()
    redirect_uri = input("Enter the Authorized Redirect URI: ").strip()
    grant_code = input("Enter a FRESH generated Grant Code: ").strip()

    if not (client_id and client_secret and redirect_uri and grant_code):
        print("\nError: All fields are required.")
        return

    # Exchange Grant Code
    token_url = f"{accounts_url.rstrip('/')}/oauth/v2/token"
    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "code": grant_code
    }

    print("\nExchanging grant code for access & refresh tokens...")
    try:
        response = requests.post(token_url, data=payload, timeout=10)
        response.raise_for_status()
        token_data = response.json()
    except Exception as e:
        print(f"Failed to exchange grant code: {e}")
        if 'response' in locals() and hasattr(response, 'text'):
            print(f"Server response: {response.text}")
        return

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")

    if not refresh_token:
        print("\nError: Did not receive a refresh token. Make sure your grant code is valid, not expired, and you used a fresh code.")
        print(f"Token response: {token_data}")
        return

    print("Success: Received Refresh Token!")

    # Update .env
    updates = {
        "ZOHO_CLIENT_ID": client_id,
        "ZOHO_CLIENT_SECRET": client_secret,
        "ZOHO_REFRESH_TOKEN": refresh_token,
        "ZOHO_ACCOUNTS_URL": accounts_url,
        "ZOHO_API_URL": api_url
    }

    if update_env_file(updates):
        print("\n[SUCCESS] Updated your .env file with client credentials and refresh token!")
    else:
        print("\n[WARNING] Could not update .env automatically. Please copy the values manually.")

    print("\n=== HOW TO LOCATE YOUR ZOHO_ZSOID ===")
    print("Your ZSOID is your Zoho Meeting Organization ID. You can find it manually:")
    print("1. Log in to your Zoho Meeting dashboard: https://meeting.zoho.com/")
    print("2. Click your profile picture/icon in the TOP-RIGHT corner of the page.")
    print("3. In the dropdown menu that appears, look for 'Org ID' or 'Organization ID'. It is a number (e.g., 600123456).")
    print("4. Copy that number, open your '.env' file, and set it as:")
    print("   ZOHO_ZSOID=your_number_here")
    print("======================================")

if __name__ == "__main__":
    main()
