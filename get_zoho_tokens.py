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
        applied_keys = set()
        for line in lines:
            line_stripped = line.strip()
            updated = False
            for key, val in updates.items():
                if line_stripped.startswith(f"{key}="):
                    new_lines.append(f"{key}={val}\n")
                    updated = True
                    applied_keys.add(key)
                    break
            if not updated:
                new_lines.append(line)

        # Append keys that were not found in the file
        for key, val in updates.items():
            if key not in applied_keys:
                new_lines.append(f"{key}={val}\n")

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        return True
    except Exception as e:
        print(f"Error updating .env file: {e}")
        return False

def read_env_values():
    values = {}
    if os.path.exists(".env"):
        try:
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, val = line.split("=", 1)
                        values[key.strip()] = val.strip()
        except Exception:
            pass
    return values


def main():
    print("=== Zoho Meeting Integration Setup Helper ===")
    print("This script will exchange a Zoho OAuth Grant Code for a Refresh Token")
    print("and automatically update your .env file.\n")

    env_values = read_env_values()
    
    default_accounts = env_values.get("ZOHO_ACCOUNTS_URL") or "https://accounts.zoho.com"
    default_api = env_values.get("ZOHO_API_URL") or "https://meeting.zoho.com"
    default_client_id = env_values.get("ZOHO_CLIENT_ID") or ""
    default_client_secret = env_values.get("ZOHO_CLIENT_SECRET") or ""

    accounts_url = input(f"Zoho Accounts URL [default: {default_accounts}]: ").strip() or default_accounts
    api_url = input(f"Zoho Meeting API URL [default: {default_api}]: ").strip() or default_api

    print(f"Detected Client ID: {default_client_id[:15]}...")
    client_id = input(f"Enter your Zoho Client ID [Press Enter to keep detected]: ").strip() or default_client_id
    
    client_secret_display = f"{default_client_secret[:10]}..." if default_client_secret else "None"
    print(f"Detected Client Secret: {client_secret_display}")
    client_secret = input(f"Enter your Zoho Client Secret [Press Enter to keep detected]: ").strip() or default_client_secret
    
    redirect_uri = input("Enter the Authorized Redirect URI [e.g. https://localhost]: ").strip()
    grant_code = input("Enter a FRESH generated Grant Code: ").strip()

    # Clean up prefixes if user accidentally pasted the whole line from .env
    if client_id.startswith("ZOHO_CLIENT_ID="):
        client_id = client_id.replace("ZOHO_CLIENT_ID=", "", 1).strip()
    if client_secret.startswith("ZOHO_CLIENT_SECRET="):
        client_secret = client_secret.replace("ZOHO_CLIENT_SECRET=", "", 1).strip()

    if not (client_id and client_secret and redirect_uri and grant_code):
        print("\nError: All fields (Client ID, Client Secret, Redirect URI, and Grant Code) are required.")
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

    # Programmatically fetch ZUID and ZSOID using the newly obtained access token
    print("\nAttempting to automatically retrieve your Zoho User ID (ZUID) and Org ID (ZSOID)...")
    try:
        user_url = f"{api_url.rstrip('/')}/api/v2/user.json"
        user_headers = {
            "Authorization": f"Zoho-oauthtoken {access_token}",
            "Content-Type": "application/json"
        }
        user_res = requests.get(user_url, headers=user_headers, timeout=10)
        if user_res.status_code == 200:
            user_data = user_res.json()
            details = user_data.get("userDetails", {})
            zuid = details.get("zuid")
            zsoid = details.get("zsoid")
            if zuid:
                updates["ZOHO_ZUID"] = str(zuid)
                print(f"-> Found ZUID (Presenter ID): {zuid}")
            if zsoid:
                updates["ZOHO_ZSOID"] = str(zsoid)
                print(f"-> Found ZSOID (Organization ID): {zsoid}")
        else:
            print(f"-> Could not retrieve user details automatically (Status {user_res.status_code}): {user_res.text}")
            print("-> Make sure your Grant Code was generated with the 'ZohoMeeting.user.READ' scope.")
    except Exception as e:
        print(f"-> Error retrieving user details: {e}")

    if update_env_file(updates):
        print("\n[SUCCESS] Updated your .env file with client credentials, refresh token, ZUID, and ZSOID!")
    else:
        print("\n[WARNING] Could not update .env automatically. Please copy the values manually.")

    print("\n=== SETUP COMPLETE ===")
    print("If the automatic discovery succeeded, all required variables have been saved to your .env file.")
    print("If not, please ensure ZOHO_ZUID and ZOHO_ZSOID are manually set in your .env file.")
    print("=======================")

if __name__ == "__main__":
    main()
