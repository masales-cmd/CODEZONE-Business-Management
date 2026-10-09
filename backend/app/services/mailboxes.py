"""Company mailbox integration using cPanel UAPI. Secrets are never returned to clients."""
from __future__ import annotations
import re
import httpx
from app.core.config import settings

class MailboxProvisionError(RuntimeError):
    pass

def cpanel_ready() -> bool:
    base=(settings.cpanel_base_url or '').strip()
    return (str(settings.email_provider or 'none').strip().lower() == 'cpanel'
            and base.startswith('https://')
            and bool((settings.cpanel_username or '').strip())
            and bool((settings.cpanel_api_token or '').strip()))

def provider_status() -> dict:
    if cpanel_ready():
        return {'provider':'cpanel','ready':True,'label':'cPanel mailbox provisioning is configured'}
    if str(settings.email_provider or 'none').strip().lower() == 'cpanel':
        return {'provider':'cpanel','ready':False,'label':'cPanel is selected but its HTTPS URL, username or API token is missing'}
    if str(settings.email_provider or 'none').strip().lower() not in ('','none','disabled'):
        return {'provider':str(settings.email_provider),'ready':False,'label':'This mailbox provider is not supported by the current integration'}
    return {'provider':'none','ready':False,'label':'Email address generation is available; real mailboxes need a configured provider'}

def _email_parts(email: str) -> tuple[str,str]:
    value=(email or '').strip().lower()
    if value.count('@') != 1:
        raise MailboxProvisionError('A valid company email address is required.')
    local, domain=value.split('@',1)
    if not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}", local):
        raise MailboxProvisionError('The email address has an invalid local part.')
    if not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', domain):
        raise MailboxProvisionError('The email address has an invalid domain.')
    return local,domain

def _uapi_call(function: str, params: dict) -> dict:
    if not cpanel_ready():
        raise MailboxProvisionError('Actual mailbox provisioning is not configured. Configure cPanel UAPI in the private CODEZONE config.env file.')
    base=settings.cpanel_base_url.strip().rstrip('/')
    # Support either the host origin or a configured cPanel base path without allowing a secret over plain HTTP.
    url=base + '/execute/Email/' + function
    headers={'Authorization':f'cpanel {settings.cpanel_username.strip()}:{settings.cpanel_api_token.strip()}','Accept':'application/json'}
    try:
        # Send password-bearing parameters in the request body rather than the URL query string.
        response=httpx.post(url,data=params,headers=headers,timeout=max(5,min(int(settings.cpanel_timeout_seconds or 20),60)),follow_redirects=False)
        response.raise_for_status()
        data=response.json()
    except httpx.TimeoutException as exc:
        raise MailboxProvisionError('The email provider timed out. Check cPanel URL and network access.') from exc
    except httpx.HTTPStatusError as exc:
        raise MailboxProvisionError(f'The email provider rejected the request (HTTP {exc.response.status_code}). Check cPanel credentials and permissions.') from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise MailboxProvisionError('Could not communicate with the email provider. Check cPanel URL, TLS certificate and network access.') from exc
    result=(data or {}).get('result') or {}
    if result.get('status') != 1:
        # Do not expose raw provider response, tokens, or server-specific information.
        raise MailboxProvisionError('cPanel could not complete the mailbox request. Check that the domain is hosted on this cPanel account and that the address is not already in use.')
    return result

def create_mailbox(email: str, password: str, quota_mb: int|None=None) -> dict:
    local,domain=_email_parts(email)
    if len(password or '') < 12:
        raise MailboxProvisionError('Mailbox password must be at least 12 characters.')
    quota=int(quota_mb if quota_mb is not None else settings.cpanel_default_quota_mb or 1024)
    if quota < 0 or quota > 1_000_000:
        raise MailboxProvisionError('Mailbox quota must be between 0 (unlimited) and 1000000 MB.')
    result=_uapi_call('add_pop',{'email':local,'password':password,'quota':quota,'domain':domain})
    return {'provider':'cpanel','external_id':email.lower(),'status':'provisioned','message':'Mailbox created by cPanel.'}


def change_mailbox_password(email: str, password: str) -> dict:
    """Rotate a mailbox password without deleting the mailbox or its stored messages."""
    local,domain=_email_parts(email)
    if len(password or '') < 12:
        raise MailboxProvisionError('Mailbox password must be at least 12 characters.')
    _uapi_call('passwd_pop',{'email':local,'password':password,'domain':domain})
    return {'provider':'cpanel','external_id':email.lower(),'status':'password_changed'}
