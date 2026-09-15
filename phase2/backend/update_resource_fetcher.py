import re

with open('app/api/v1/resource_fetcher.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace httpx.AsyncClient with AuditedHTTPClient
content = content.replace(
'''    async with httpx.AsyncClient(timeout=30.0) as client:
        # Try /user/repos first (works with OAuth user tokens)
        try:
            resp = await client.get(
                "https://api.github.com/user/repos",
                headers=headers,
                params={"per_page": 100, "sort": "updated", "direction": "desc"},
            )
            if resp.is_success:''',
'''    async with AuditedHTTPClient(provider="github", access_token=access_token, audit_callback=audit_callback) as client:
        # Try /user/repos first (works with OAuth user tokens)
        try:
            resp = await client.get(
                "https://api.github.com/user/repos",
                params={"per_page": 100, "sort": "updated", "direction": "desc"},
                operation="github_fetch_user_repos"
            )
            if resp:'''
)

# And the fallback:
content = content.replace(
'''        # Fallback: try /installation/repositories (for installation tokens)
        try:
            resp = await client.get(
                "https://api.github.com/installation/repositories",
                headers=headers,
                params={"per_page": 100},
            )
            if resp.is_success:''',
'''        # Fallback: try /installation/repositories (for installation tokens)
        try:
            resp = await client.get(
                "https://api.github.com/installation/repositories",
                params={"per_page": 100},
                operation="github_fetch_installation_repos"
            )
            if resp:'''
)

# Replace resp.json() inside the try block for /user/repos
content = content.replace(
'''            if resp:
                repos = resp.json()''',
'''            if resp:
                repos = resp'''
)
content = content.replace(
'''            if resp.is_success:
                repos = resp.json()''',
'''            if True:
                repos = resp'''
)
content = content.replace(
'''            if resp.is_success:
                data = resp.json()''',
'''            if resp:
                data = resp'''
)
content = content.replace(
'''            if True:
                data = resp.json()''',
'''            if True:
                data = resp'''
)

# Also fix the else block
content = content.replace(
'''            else:
                logger.warning(
                    "github_installation_repos_also_failed",
                    status_code=resp.status_code,
                )''',
'''            # AuditedHTTPClient throws on failure, so else is unreachable but keeping for logic'''
)
content = content.replace(
'''            logger.warning(
                "github_user_repos_failed",
                status_code=resp.status_code,
            )''',
'''            pass'''
)

# Note: AuditedHTTPClient handles the github headers (Accept, X-GitHub-Api-Version) internally or ignores them for generic requests,
# but it automatically passes the Bearer token!

with open('app/api/v1/resource_fetcher.py', 'w', encoding='utf-8') as f:
    f.write(content)
