"""Display the local pairing token intentionally to the user; never a Gemini key."""
import argparse


def main():
    parser = argparse.ArgumentParser(description='Pair the local Video Analyzer app with Premiere')
    parser.add_argument('--rotate', action='store_true', help='Revoke previous panel pairings')
    args = parser.parse_args()
    from app.services.editor_bridge import get_pairing_token
    print(get_pairing_token(rotate=args.rotate))


if __name__ == '__main__':
    main()
