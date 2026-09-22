"""Acquisition adapters used for source naming."""

from __future__ import annotations


class CareerSource:
    name = "careers"
    display_name = "Company careers page"

    def scrape(self, *args, **kwargs):
        raise NotImplementedError("Use pipeline.collect_jobs() instead.")


def get_default_source() -> CareerSource:
    return CareerSource()


__all__ = ["CareerSource", "get_default_source"]
