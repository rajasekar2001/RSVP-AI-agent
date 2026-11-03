"""Regression checks for the first spoken message and CSV campaign export."""
import asyncio
import csv
import io

import app


def test_csv_names_have_distinct_openings():
    state = {
        "agent": {"prompt": "Event name: Family Celebration\nEvent date: Saturday, 14 November 2026\nEvent time: 6:30 PM\nEvent venue: Community Hall"},
        "knowledge": [],
    }
    for name in ("Raja", "Bhushan", "Ganesun"):
        context = app.build_real_call_context(state, {"name": name})
        assert f"Am I speaking with {name}?" in context["opening_message"]
        assert "Family Celebration" in context["opening_message"]
        assert "Community Hall" in context["opening_message"]
        assert "WAIT for an answer" in context["call_instructions"]
        assert "Never read a list of CSV names" in context["call_instructions"]
        for other in ("Raja", "Bhushan", "Ganesun"):
            if other != name:
                assert other not in context["opening_message"]


def test_unstructured_event_brief_fields():
    state = {"agent": {"prompt": "RSVP calls"}, "knowledge": [{"name": "brief", "text": "Date: Saturday, 14 November 2026\nTime: 6:30 PM\nVenue: Town Hall"}]}
    _, facts = app.intro_event_details(state)
    assert "Date: Saturday, 14 November 2026" in facts
    assert "Time: 6:30 PM" in facts
    assert "Venue: Town Hall" in facts


if __name__ == "__main__":
    test_csv_names_have_distinct_openings()
    test_unstructured_event_brief_fields()
    print("Live call context tests passed")
