import logging


def configure(level):
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
