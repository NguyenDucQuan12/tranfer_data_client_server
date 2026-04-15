from __future__ import annotations

import argparse
from pathlib import Path

import requests

from client.common import get_token
from shared.config import settings



def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--username', required=True)
    parser.add_argument('--password', required=True)
    parser.add_argument('file_path')
    args = parser.parse_args()

    token = get_token(args.username, args.password)['access_token']
    file_path = Path(args.file_path)
    if not file_path.exists():
        print(f'Không tìm thấy file: {file_path}')
        raise SystemExit(1)

    with file_path.open('rb') as fh:
        response = requests.post(
            f'{settings.API_BASE_URL}/v1/jobs/upload',
            headers={'Authorization': f'Bearer {token}'},
            files={'file': (file_path.name, fh)},
            timeout=60,
        )

    print('Status code:', response.status_code)
    print(response.text)


if __name__ == '__main__':
    main()
