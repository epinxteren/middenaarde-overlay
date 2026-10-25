import sys
if '--self-test' in sys.argv:
    import selftest
    from pathlib import Path
    path = Path(sys.argv[sys.argv.index('--self-test') + 1])
    try:
        selftest.run(path)
    except Exception:
        import traceback
        path.write_text(traceback.format_exc(), encoding='utf-8')
        raise
else:
    from overlay import main
    main()
