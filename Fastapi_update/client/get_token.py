from __future__ import annotations

import json
import sys

from client.common import get_token



def main() -> None:
    if len(sys.argv) != 3:
        print('Cách dùng: python client/get_token.py <username> <password>')
        raise SystemExit(1)

    data = get_token(sys.argv[1], sys.argv[2])
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
