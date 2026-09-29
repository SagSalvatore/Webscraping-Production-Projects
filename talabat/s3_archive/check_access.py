"""What can these credentials actually do on s3://rii-data-dump? Run this first.

Tech granted S3 access only, so the useful question is not "am I an admin" but
"which of the six calls this archive makes are allowed". This tries each one in
turn against a throwaway key under talabat/_preflight/ and prints allowed or
denied. It uploads about 30 bytes and deletes them again.

  identity            who the credentials belong to (needs sts, may be denied)
  HeadBucket          the bucket exists and is reachable from this region
  ListObjectsV2       needed by verify_s3.py to see what is already there
  PutObject           the upload itself
  GetObject           needed by verify_s3.py --deep and by restore_from_s3.py
  DeleteObject        only ever used to clean up this preflight object
  bucket settings     versioning / lifecycle / encryption - read-only check;
                      denied is FINE, it just means Tech owns those switches

    python check_access.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")
from layout import BUCKET, REGION, ROOT_PREFIX  # noqa: E402
from upload_to_s3 import client  # noqa: E402

KEY = f"{ROOT_PREFIX}/_preflight/access_check.txt"
BODY = b"talabat archive preflight\n"


def try_call(label, fn, needed_for):
    try:
        fn()
        print(f"  ALLOWED  {label:22} {needed_for}")
        return True
    except Exception as exc:
        code = getattr(getattr(exc, "response", {}), "get", lambda *_: {})("Error", {}).get("Code", "")
        print(f"  DENIED   {label:22} {needed_for}")
        print(f"           {code or type(exc).__name__}: {str(exc)[:120]}")
        return False


def main():
    s3 = client()
    print("=" * 78)
    print(f"  ACCESS CHECK  s3://{BUCKET}  region {REGION}")
    print("=" * 78)
    try:
        import boto3
        who = boto3.client("sts", region_name=REGION).get_caller_identity()
        print(f"  identity: {who['Arn']}\n            account {who['Account']}")
    except Exception as exc:
        print(f"  identity: could not read (sts denied or no credentials) - {str(exc)[:90]}")

    ok = {}
    ok["head"] = try_call("HeadBucket", lambda: s3.head_bucket(Bucket=BUCKET), "reach the bucket")
    ok["list"] = try_call("ListObjectsV2",
                          lambda: s3.list_objects_v2(Bucket=BUCKET, Prefix=ROOT_PREFIX + "/", MaxKeys=1),
                          "verify + see what is uploaded")
    ok["put"] = try_call("PutObject",
                         lambda: s3.put_object(Bucket=BUCKET, Key=KEY, Body=BODY,
                                               ChecksumAlgorithm="SHA256"), "the upload itself")
    if ok["put"]:
        ok["get"] = try_call("GetObject", lambda: s3.get_object(Bucket=BUCKET, Key=KEY)["Body"].read(),
                             "deep verify + restore")
        ok["head_obj"] = try_call("HeadObject", lambda: s3.head_object(Bucket=BUCKET, Key=KEY),
                                  "verify size + checksum")
        ok["delete"] = try_call("DeleteObject", lambda: s3.delete_object(Bucket=BUCKET, Key=KEY),
                                "clean up this check only")
    print("\n  bucket settings (read-only; DENIED here is fine - Tech owns them)")
    try_call("GetBucketVersioning", lambda: s3.get_bucket_versioning(Bucket=BUCKET), "undo for overwrites")
    try_call("GetBucketLifecycle", lambda: s3.get_bucket_lifecycle_configuration(Bucket=BUCKET),
             "storage class transitions")
    try_call("GetBucketEncryption", lambda: s3.get_bucket_encryption(Bucket=BUCKET), "encryption at rest")

    need = ["head", "list", "put", "get", "head_obj"]
    missing = [k for k in need if not ok.get(k)]
    print("\n" + "=" * 78)
    if not missing:
        print("  READY - upload, verify and restore will all work.")
        print("  next: python upload_to_s3.py --execute --max-gb 2")
    else:
        print(f"  NOT READY - these are required and denied: {', '.join(missing)}")
        print("  Send Tech the policy in README.md (section 'What to ask Tech for').")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
