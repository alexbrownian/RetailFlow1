"""
Reddit API starter script (official OAuth method).

Before running:
1. Go to https://www.reddit.com/prefs/apps (logged in) and click
   "create another app...". Choose type "script".
   - name: anything (e.g. my-first-api-script)
   - redirect uri: http://localhost:8080  (required field, not used by us)
2. After creating it, copy:
   - the string under the app name  -> CLIENT_ID
   - the "secret"                   -> CLIENT_SECRET
3. Fill in the three variables below, then run:  python reddit_api_starter.py
"""

import requests

# ---- STEP 0: your credentials (fill these in) ----
CLIENT_ID = "PASTE_YOUR_CLIENT_ID"
CLIENT_SECRET = "PASTE_YOUR_SECRET"
REDDIT_USERNAME = "your_reddit_username"  # only used in the User-Agent text

# Reddit REQUIRES a descriptive User-Agent, or it will block you with 429 errors.
# Format convention: <platform>:<app name>:<version> (by /u/<username>)
USER_AGENT = "windows:my-first-api-script:v1.0 (by /u/" + REDDIT_USERNAME + ")"


def get_access_token():
    """STEP 1: Trade your client id + secret for a temporary access token."""
    # HTTP Basic Auth = "prove who you are" using client_id as the username
    # and client_secret as the password.
    auth = requests.auth.HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET)

    # "client_credentials" = app-only access (read public data, no user login).
    data = {"grant_type": "client_credentials"}

    headers = {"User-Agent": USER_AGENT}

    response = requests.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=auth,
        data=data,
        headers=headers,
    )

    if response.status_code != 200:
        print("Token request failed! Status:", response.status_code)
        print("Response body:", response.text)
        return None

    # Reddit replies with JSON like:
    # {"access_token": "abc123...", "token_type": "bearer", "expires_in": 86400}
    token = response.json()["access_token"]
    return token


def get_hot_posts(token, subreddit, limit):
    """STEP 2: Use the token to call the real API (note: oauth.reddit.com)."""
    headers = {
        "Authorization": "bearer " + token,  # this is how Reddit knows it's you
        "User-Agent": USER_AGENT,
    }

    url = "https://oauth.reddit.com/r/" + subreddit + "/hot"
    params = {"limit": limit}

    response = requests.get(url, headers=headers, params=params)

    if response.status_code != 200:
        print("API call failed! Status:", response.status_code)
        print("Response body:", response.text)
        return None

    # STEP 3: check your rate limit budget (Reddit sends it back every call)
    print("Requests remaining this window:", response.headers.get("x-ratelimit-remaining"))

    return response.json()


def main():
    token = get_access_token()
    if token is None:
        return
    print("Got access token:", token[:10] + "...")  # print first 10 chars only

    result = get_hot_posts(token, "personalfinance", limit=5)
    if result is None:
        return

    # The JSON structure is: result["data"]["children"] = list of posts,
    # and each post's real info lives in post["data"].
    posts = result["data"]["children"]
    print("\nTop 5 hot posts in r/personalfinance:")
    for post in posts:
        info = post["data"]
        print(str(info["score"]) + " points | " + info["title"])


if __name__ == "__main__":
    main()
