"""Compatibility entry point for the Qt controller (no legacy Tk dependency)."""
from ur10_vision_qt_app import main

if __name__ == '__main__':
    raise SystemExit(main())
