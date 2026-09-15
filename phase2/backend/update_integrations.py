import re

with open('app/api/v1/integrations.py', 'r', encoding='utf-8') as f:
    content = f.read()

helper_func = '''
async def _get_capped_expires_at(session: AsyncSession, client_id: uuid.UUID, token_expires_in: int | None = None) -> datetime | None:
    client = await session.get(Client, client_id)
    if not client:
        return None
    
    now = datetime.now(timezone.utc)
    token_expiry = now + timedelta(seconds=token_expires_in) if token_expires_in else None
    
    if client.engagement_end:
        if token_expiry:
            return min(token_expiry, client.engagement_end)
        return client.engagement_end
    return token_expiry
'''

if '_get_capped_expires_at' not in content:
    content = content.replace('# ── Audit Logging Helper ──', helper_func + '\n\n# ── Audit Logging Helper ──')


# 1. finalize_google_oauth
# Needs to set expires_at
content = content.replace(
'''            existing.granted_scopes = granted_scopes
            existing.connected_at = datetime.now(timezone.utc)''',
'''            existing.granted_scopes = granted_scopes
            existing.connected_at = datetime.now(timezone.utc)
            existing.expires_at = await _get_capped_expires_at(session, client_id, token_data.get("expires_in"))'''
)
content = content.replace(
'''                granted_scopes=granted_scopes,
                secret_ref=secret_ref,
                connected_at=datetime.now(timezone.utc),
                provider_metadata=meta if meta else None,''',
'''                granted_scopes=granted_scopes,
                secret_ref=secret_ref,
                connected_at=datetime.now(timezone.utc),
                expires_at=await _get_capped_expires_at(session, client_id, token_data.get("expires_in")),
                provider_metadata=meta if meta else None,'''
)

# 2. finalize_meta_oauth
content = content.replace(
'''            existing.granted_scopes = scopes
            existing.connected_at = datetime.now(timezone.utc)''',
'''            existing.granted_scopes = scopes
            existing.connected_at = datetime.now(timezone.utc)
            existing.expires_at = await _get_capped_expires_at(session, client_id, token_data.get("expires_in"))'''
)
content = content.replace(
'''                scopes=scopes,
                secret_ref=secret_ref,
                connected_at=datetime.now(timezone.utc),
                provider_metadata=meta if meta else None,''',
'''                scopes=scopes,
                secret_ref=secret_ref,
                connected_at=datetime.now(timezone.utc),
                expires_at=await _get_capped_expires_at(session, client_id, token_data.get("expires_in")),
                provider_metadata=meta if meta else None,'''
)

# 3. finalize_github_oauth
content = content.replace(
'''            existing.scopes = scopes
            existing.connected_at = datetime.now(timezone.utc)''',
'''            existing.scopes = scopes
            existing.connected_at = datetime.now(timezone.utc)
            existing.expires_at = await _get_capped_expires_at(session, client_id, token_data.get("expires_in"))'''
)
content = content.replace(
'''                scopes=scopes,
                secret_ref=secret_ref,
                connected_at=datetime.now(timezone.utc),
                provider_metadata=meta if meta else None,''',
'''                scopes=scopes,
                secret_ref=secret_ref,
                connected_at=datetime.now(timezone.utc),
                expires_at=await _get_capped_expires_at(session, client_id, token_data.get("expires_in")),
                provider_metadata=meta if meta else None,'''
)

# 4. finalize_connection (generic / CMP)
content = content.replace(
'''            existing.scopes = scopes
            existing.secret_ref = secret_ref
            existing.connected_at = datetime.now(timezone.utc)
            connection = existing''',
'''            existing.scopes = scopes
            existing.secret_ref = secret_ref
            existing.connected_at = datetime.now(timezone.utc)
            existing.expires_at = await _get_capped_expires_at(session, client_id)
            connection = existing'''
)
content = content.replace(
'''        provider=data.provider,
        status=ConnectionStatus.CONNECTED,
        scopes=scopes,
        secret_ref=secret_ref,
        connected_at=datetime.now(timezone.utc),
        provider_metadata=metadata,''',
'''        provider=data.provider,
        status=ConnectionStatus.CONNECTED,
        scopes=scopes,
        secret_ref=secret_ref,
        connected_at=datetime.now(timezone.utc),
        expires_at=await _get_capped_expires_at(session, client_id),
        provider_metadata=metadata,'''
)


with open('app/api/v1/integrations.py', 'w', encoding='utf-8') as f:
    f.write(content)
