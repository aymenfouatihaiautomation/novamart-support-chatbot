"""Uploade les documents de `documents/` vers un bucket S3.

Usage :
    python scripts/upload_to_s3.py --bucket mon-bucket --prefix novamart/

Le bucket sert de source de donnees pour la Knowledge Base Bedrock.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import config  # noqa: E402

DOCUMENTS_DIR = Path(__file__).resolve().parents[1] / "documents"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload NovaMart documents to S3")
    parser.add_argument("--bucket", required=True, help="Nom du bucket S3 cible")
    parser.add_argument("--prefix", default="novamart/", help="Prefixe des cles S3")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # L'upload S3 n'a besoin que des credentials AWS, pas de l'ID de Knowledge Base.
    if not (
        config._is_real_value(config.AWS_ACCESS_KEY_ID)
        and config._is_real_value(config.AWS_SECRET_ACCESS_KEY)
    ):
        print("[ERREUR] Credentials AWS non configures (voir .env).")
        return 1

    import boto3

    s3 = boto3.client("s3", **config.boto3_client_kwargs())

    files = sorted(DOCUMENTS_DIR.glob("*.txt"))
    if not files:
        print(f"[ERREUR] Aucun .txt trouve dans {DOCUMENTS_DIR}")
        return 1

    for path in files:
        key = f"{args.prefix}{path.name}"
        print(f"Upload {path.name} -> s3://{args.bucket}/{key}")
        s3.upload_file(str(path), args.bucket, key)

    print(f"\n{len(files)} fichier(s) uploade(s). Pensez a resynchroniser la Knowledge Base.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
