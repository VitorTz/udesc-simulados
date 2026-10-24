"""Private Cloudflare R2 objects using its S3-compatible interface."""
from __future__ import annotations
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from functools import lru_cache
from pathlib import Path
from settings import r2_config


@lru_cache(maxsize=1)
def _client_and_bucket():
    cfg = r2_config()
    client = boto3.client(
        's3', endpoint_url=cfg['endpoint'], region_name='auto',
        aws_access_key_id=cfg['key_id'], aws_secret_access_key=cfg['secret'],
        config=Config(signature_version='s3v4', retries={'max_attempts': 3, 'mode': 'standard'}),
    )
    return client, cfg['bucket']


def upload_file(local_file: Path, object_key: str, content_type='application/pdf') -> None:
    client, bucket = _client_and_bucket()
    client.upload_file(str(local_file), bucket, object_key,
                       ExtraArgs={'ContentType': content_type})


def upload_bytes(data: bytes, object_key: str, content_type='image/png') -> None:
    client, bucket = _client_and_bucket()
    client.put_object(Bucket=bucket, Key=object_key, Body=data, ContentType=content_type)


def get_bytes(object_key: str) -> bytes:
    client, bucket = _client_and_bucket()
    response = client.get_object(Bucket=bucket, Key=object_key)
    try:
        return response['Body'].read()
    finally:
        response['Body'].close()


def get_optional_bytes(object_key: str) -> bytes | None:
    try:
        return get_bytes(object_key)
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') in ('NoSuchKey', '404', 'NotFound'):
            return None
        raise


def delete_object(object_key: str) -> None:
    client, bucket = _client_and_bucket()
    client.delete_object(Bucket=bucket, Key=object_key)


def delete_prefix(prefix: str) -> None:
    """Remove rendered caches under a unique document/edition prefix."""
    client, bucket = _client_and_bucket()
    paginator = client.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        objects = page.get('Contents', [])
        for offset in range(0, len(objects), 1000):
            client.delete_objects(Bucket=bucket, Delete={'Objects': [
                {'Key': obj['Key']} for obj in objects[offset:offset+1000]
            ]})
