#!/usr/bin/env python3
"""Generate a reviewed FreePackages configuration fragment (no Steam credentials)."""
import argparse
import json


def profile(only_temporary=True, pause_while_playing=True):
    return {
        "EnableFreePackages": True,
        "PauseFreePackagesWhilePlaying": bool(pause_while_playing),
        "FreePackagesLimit": 15,
        "FreePackagesFilters": [{
            "NoCostOnly": bool(only_temporary),
            "IgnoredTypes": ["Demo"],
            "IgnoreFreeWeekends": True,
            "PlaytestMode": 0
        }],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-free",action="store_true",help="Include permanently free packages (default only time-limited NoCost promotions)")
    args = parser.parse_args()
    print(json.dumps(profile(not args.all_free),indent=2))
    print("\n# Merge this fragment into the friend's own ASF bot config. Never put credentials into FreebieHarbor.",file=__import__("sys").stderr)


if __name__=="__main__":
    main()
