"""Export the game data of an installation into one ``catalog.json``.

The server reads the player's own ``item_defs`` every time it starts, which is
the most faithful source but also the slowest part of a start (a few seconds).
This tool writes everything it needs into a single JSON file, so the server can
run from that file (``python3 -m server --catalog catalog.json``) on a machine
without the game installed - handy for tests and for editing the event data.

Usage::

    python3 tools/extract_client_catalog.py --client /path/to/World_of_Tanks
    python3 tools/extract_client_catalog.py --client /path --out catalog.json
    python3 tools/extract_client_catalog.py --client /path --no-vehicles
"""

from __future__ import annotations

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from server import loader  # noqa: E402


def build_payload(source: str, include_vehicles: bool = True) -> dict:
    """The JSON export of the catalogue read from ``source``."""
    catalog = loader.load_catalog(source)
    catalog.validate()
    payload = catalog.to_json()
    if not include_vehicles:
        payload["vehicles"] = []
    payload["source"] = os.path.abspath(source)
    return payload


def extract(source: str, target: str = None, include_vehicles: bool = True) -> str:
    """Write the export of ``source`` to ``target``; returns the path written."""
    payload = build_payload(source, include_vehicles=include_vehicles)
    if target is None:
        target = os.path.join(REPO_ROOT, "server", "data", "catalog.json")
    directory = os.path.dirname(os.path.abspath(target))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=1)
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", help="path to the World of Tanks installation")
    parser.add_argument("--out", help="file to write (default: server/data/catalog.json)")
    parser.add_argument("--no-vehicles", action="store_true",
                        help="export the New Year definitions only")
    args = parser.parse_args(argv)

    source = args.client or loader.guess_client()
    if not source:
        raise SystemExit("no World of Tanks installation found; pass --client")
    print("reading game data from %s" % loader.source_description(source))
    target = extract(source, args.out, include_vehicles=not args.no_vehicles)
    size = os.path.getsize(target)
    payload = json.load(open(target, encoding="utf-8"))
    print("wrote %s (%d KiB): %d vehicles, %d decorations, %d boxes, %d chests"
          % (target, size // 1024, len(payload.get("vehicles", ())),
             len(payload["newYear"].get("toys", {})),
             len(payload["newYear"].get("boxes", {})),
             len(payload["newYear"].get("chests", {}))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
