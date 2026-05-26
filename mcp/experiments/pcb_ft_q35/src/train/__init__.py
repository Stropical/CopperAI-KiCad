"""Training entrypoints for staged synthetic scaffolding."""


def stage1_main() -> None:
    from .train_stage1 import main

    main()


def stage2_main() -> None:
    from .train_stage2 import main

    main()


__all__ = ["stage1_main", "stage2_main"]
