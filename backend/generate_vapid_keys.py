"""
Run this once, locally, to generate a VAPID key pair for Web Push
notifications (this is what lets your backend send real push notifications
that work even when the browser is closed).

    pip install cryptography
    python generate_vapid_keys.py

It prints two values. Put them in Render's Environment tab as:
  VAPID_PRIVATE_KEY_PEM  -- the whole PEM block, BEGIN/END lines included
  VAPID_PUBLIC_KEY       -- the single base64url line

Keep the private key secret (Render env var only, never in the frontend or
the repo). The public key is safe to expose -- the backend serves it to the
frontend automatically via the /vapid-public-key endpoint, so you don't need
to touch frontend/index.html for this.
"""
import base64
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

private_key = ec.generate_private_key(ec.SECP256R1())
public_key = private_key.public_key()

private_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()

public_raw = public_key.public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.UncompressedPoint,
)
public_b64url = base64.urlsafe_b64encode(public_raw).rstrip(b"=").decode()

print("=" * 70)
print("VAPID_PRIVATE_KEY_PEM -- paste this whole block (BEGIN/END included)")
print("as one Render environment variable:")
print("=" * 70)
print(private_pem)
print("=" * 70)
print("VAPID_PUBLIC_KEY -- paste this single line as another Render")
print("environment variable:")
print("=" * 70)
print(public_b64url)
print("=" * 70)
