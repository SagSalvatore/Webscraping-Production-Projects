"""Put s3://rii-data-dump into the state this archive assumes. Dry run by default.

  1 the bucket exists and we can reach it
  2 Block Public Access ON, all four switches - this data is the client's
  3 default encryption SSE-S3 (AES256)
  4 versioning ON - the archive's undo. prune_local.py deletes the local copy
    only; a bad overwrite in S3 stays recoverable
  5 the lifecycle policy in bucket_lifecycle.json - what makes 15 GB cost cents

Nothing here deletes or overwrites data; the worst it does is change bucket
settings, and each change is printed before it is made.

    python setup_bucket.py                # show what would change
    python setup_bucket.py --execute
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")
from layout import BUCKET  # noqa: E402
from upload_to_s3 import client  # noqa: E402

LIFECYCLE = HERE / "bucket_lifecycle.json"


def main(args):
    s3 = client()
    print("=" * 78)
    print(f"  BUCKET SETUP  s3://{BUCKET}   {'EXECUTE' if args.execute else 'DRY RUN'}")
    print("=" * 78)
    try:
        s3.head_bucket(Bucket=BUCKET)
        print("  bucket reachable                     OK")
    except Exception as exc:
        print(f"  cannot reach the bucket: {str(exc)[:140]}")
        print("  create it in the AWS console (or: aws s3 mb s3://" + BUCKET + " --region me-central-1)")
        return 1

    steps = [
        ("Block Public Access (all four)", lambda: s3.put_public_access_block(
            Bucket=BUCKET, PublicAccessBlockConfiguration={
                "BlockPublicAcls": True, "IgnorePublicAcls": True,
                "BlockPublicPolicy": True, "RestrictPublicBuckets": True})),
        ("Default encryption SSE-S3", lambda: s3.put_bucket_encryption(
            Bucket=BUCKET, ServerSideEncryptionConfiguration={"Rules": [
                {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
                 "BucketKeyEnabled": True}]})),
        ("Versioning ON", lambda: s3.put_bucket_versioning(
            Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})),
        ("Lifecycle policy from bucket_lifecycle.json", lambda: s3.put_bucket_lifecycle_configuration(
            Bucket=BUCKET, LifecycleConfiguration=json.loads(LIFECYCLE.read_text(encoding="utf-8")))),
    ]
    denied = []
    for name, fn in steps:
        if not args.execute:
            print(f"  would set: {name}")
            continue
        try:
            fn()
            print(f"  set: {name}")
        except Exception as exc:
            denied.append(name)
            print(f"  DENIED {name}: {str(exc)[:140]}")
    if not args.execute:
        print("\n  --execute to apply. Nothing was changed.")
        return 0
    if denied:
        # Expected when Tech owns the bucket and granted object access only.
        # The archive itself still works; these are the bucket's own settings.
        print("\n  These need bucket-owner rights - ask Tech to apply them once:")
        for d in denied:
            print(f"    - {d}")
        print("  Versioning matters most: it is the undo for an accidental overwrite.")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true")
    sys.exit(main(p.parse_args()))
