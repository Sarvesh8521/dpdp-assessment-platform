import re

with open('app/api/v1/oauth_handlers.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Add import
if 'from app.integrations.base.http_client import AuditedHTTPClient, OAuthError' not in content:
    content = content.replace('from app.integrations.base.http_client import OAuthError', 'from app.integrations.base.http_client import OAuthError, AuditedHTTPClient')
if 'AuditedHTTPClient' not in content:
    content = content.replace('import httpx', 'import httpx\nfrom app.integrations.base.http_client import AuditedHTTPClient')

# 1. Google exchange_code
content = content.replace(
'''        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.GOOGLE_TOKEN_URL,
                data={
                    "client_id": self.settings.google_client_id,
                    "client_secret": self.settings.google_client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.settings.google_oauth_redirect_uri,
                },
            )
            if not resp.is_success:
                logger.error("google_oauth_token_exchange_failed", status_code=resp.status_code)
                raise OAuthError(f"Google OAuth token exchange failed: {resp.status_code}")
            return resp.json()''',
'''        audit_cb = kwargs.get("audit_callback")
        async with AuditedHTTPClient(provider="google", audit_callback=audit_cb) as client:
            try:
                return await client.post(
                    self.GOOGLE_TOKEN_URL,
                    data={
                        "client_id": self.settings.google_client_id,
                        "client_secret": self.settings.google_client_secret,
                        "code": code,
                        "grant_type": "authorization_code",
                        "redirect_uri": self.settings.google_oauth_redirect_uri,
                    },
                    operation="google_oauth_exchange"
                )
            except Exception as e:
                logger.error("google_oauth_token_exchange_failed", error=str(e))
                raise OAuthError(f"Google OAuth token exchange failed: {e}")'''
)

# 2. Google refresh_access_token
content = content.replace(
'''        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.GOOGLE_TOKEN_URL,
                data={
                    "client_id": self.settings.google_client_id,
                    "client_secret": self.settings.google_client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if not resp.is_success:
                logger.error("google_token_refresh_failed", status_code=resp.status_code)
                raise OAuthError(f"Google token refresh failed: {resp.status_code}")
            return resp.json()''',
'''        audit_cb = kwargs.get("audit_callback") if "audit_callback" in kwargs else None
        async with AuditedHTTPClient(provider="google", audit_callback=audit_cb) as client:
            try:
                return await client.post(
                    self.GOOGLE_TOKEN_URL,
                    data={
                        "client_id": self.settings.google_client_id,
                        "client_secret": self.settings.google_client_secret,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                    operation="google_oauth_refresh"
                )
            except Exception as e:
                logger.error("google_token_refresh_failed", error=str(e))
                raise OAuthError(f"Google token refresh failed: {e}")'''
)

# 3. Google fetch_user_info
content = content.replace(
'''        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(
                    self.GOOGLE_USERINFO_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if resp.is_success:
                    return resp.json()
            except Exception:
                pass
            return {}''',
'''        audit_cb = kwargs.get("audit_callback")
        async with AuditedHTTPClient(provider="google", access_token=access_token, audit_callback=audit_cb) as client:
            try:
                return await client.get(self.GOOGLE_USERINFO_URL, operation="google_fetch_user_info")
            except Exception:
                return {}'''
)

# 4. Meta exchange_code
content = content.replace(
'''        async with httpx.AsyncClient() as client:
            resp = await client.get(
                self.META_TOKEN_URL,
                params={
                    "client_id": self.settings.meta_client_id,
                    "client_secret": self.settings.meta_client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
            if not resp.is_success:
                raise OAuthError(f"Meta token exchange failed: {resp.status_code}")
            return resp.json()''',
'''        audit_cb = kwargs.get("audit_callback")
        async with AuditedHTTPClient(provider="meta", audit_callback=audit_cb) as client:
            try:
                return await client.get(
                    self.META_TOKEN_URL,
                    params={
                        "client_id": self.settings.meta_client_id,
                        "client_secret": self.settings.meta_client_secret,
                        "code": code,
                        "redirect_uri": redirect_uri,
                    },
                    operation="meta_oauth_exchange"
                )
            except Exception as e:
                raise OAuthError(f"Meta token exchange failed: {e}")'''
)

# 5. Meta fetch_user_info
content = content.replace(
'''        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(
                    self.META_USERINFO_URL,
                    params={"access_token": access_token, "fields": "id,name,email"},
                )
                if resp.is_success:
                    return resp.json()
            except Exception:
                pass
            return {}''',
'''        audit_cb = kwargs.get("audit_callback")
        async with AuditedHTTPClient(provider="meta", access_token=access_token, audit_callback=audit_cb) as client:
            try:
                return await client.get(self.META_USERINFO_URL, params={"fields": "id,name,email"}, operation="meta_fetch_user_info")
            except Exception:
                return {}'''
)

# 6. GitHub exchange_code
content = content.replace(
'''        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.GITHUB_TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self.settings.github_client_id,
                    "client_secret": self.settings.github_client_secret,
                    "code": code,
                    "redirect_uri": self.settings.github_oauth_redirect_uri,
                },
            )
            if not resp.is_success:
                logger.error("github_oauth_token_exchange_failed", status_code=resp.status_code)
                raise OAuthError(f"GitHub OAuth token exchange failed: {resp.status_code}")
            return resp.json()''',
'''        audit_cb = kwargs.get("audit_callback")
        async with AuditedHTTPClient(provider="github", audit_callback=audit_cb) as client:
            try:
                return await client.post(
                    self.GITHUB_TOKEN_URL,
                    data={
                        "client_id": self.settings.github_client_id,
                        "client_secret": self.settings.github_client_secret,
                        "code": code,
                        "redirect_uri": self.settings.github_oauth_redirect_uri,
                    },
                    operation="github_oauth_exchange"
                )
            except Exception as e:
                logger.error("github_oauth_token_exchange_failed", error=str(e))
                raise OAuthError(f"GitHub OAuth token exchange failed: {e}")'''
)

# 7. GitHub fetch_user_info
content = content.replace(
'''        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(
                    self.GITHUB_USERINFO_URL,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                if resp.is_success:
                    return resp.json()
            except Exception:
                pass
            return {}''',
'''        audit_cb = kwargs.get("audit_callback")
        async with AuditedHTTPClient(provider="github", access_token=access_token, audit_callback=audit_cb) as client:
            try:
                # We don't strictly need X-GitHub-Api-Version for user info, but AuditedHTTPClient handles headers mostly.
                return await client.get(self.GITHUB_USERINFO_URL, operation="github_fetch_user_info")
            except Exception:
                return {}'''
)

# 8. GitHub get_installation_token
content = content.replace(
'''        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if not resp.is_success:
                raise OAuthError(f"GitHub installation token creation failed: {resp.status_code}")
            return resp.json()''',
'''        # We pass jwt as access_token for AuditedHTTPClient which uses Bearer
        audit_cb = kwargs.get("audit_callback")
        async with AuditedHTTPClient(provider="github", access_token=jwt_token, audit_callback=audit_cb) as client:
            try:
                return await client.post(url, operation="github_get_installation_token")
            except Exception as e:
                raise OAuthError(f"GitHub installation token creation failed: {e}")'''
)

# 9. GitHub list_installations
content = content.replace(
'''        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if not resp.is_success:
                raise OAuthError(f"Failed to fetch GitHub installations: {resp.status_code}")
            return resp.json()''',
'''        audit_cb = kwargs.get("audit_callback")
        async with AuditedHTTPClient(provider="github", access_token=access_token, audit_callback=audit_cb) as client:
            try:
                return await client.get(url, operation="github_list_installations")
            except Exception as e:
                raise OAuthError(f"Failed to fetch GitHub installations: {e}")'''
)

with open('app/api/v1/oauth_handlers.py', 'w', encoding='utf-8') as f:
    f.write(content)
