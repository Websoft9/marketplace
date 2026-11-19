"""Shared S3 location and optional client for screenshot and listing assets."""

BUCKET = "assets-797851739507-us-east-1-an"
REGION = "us-east-1"
OWNER = "797851739507"
PUBLIC_BASE = f"https://{BUCKET}.s3.{REGION}.amazonaws.com"


def create_s3_client(profile=None):
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise ValueError("install the AWS SDK with: pip install '.[aws]'") from None
    try:
        return boto3.Session(profile_name=profile).client(
            "s3", region_name=REGION,
            config=Config(connect_timeout=10, read_timeout=30,
                          retries={"mode": "standard", "max_attempts": 3}),
        )
    except Exception:
        raise ValueError("Unable to initialize S3 credentials/client.") from None
