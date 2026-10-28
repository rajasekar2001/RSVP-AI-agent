from app import agent_reply

def run():
    state = {"agent": {"prompt": "Event name: Community Dinner\nEvent date: Friday\nEvent venue: Town Hall", "voice_language": ""}, "knowledge": []}
    for name in ("Raja", "Bhushan", "Ganesun"):
        session = {"name": name, "stage": "opening"}
        intro = agent_reply(state, "", session)["reply"]
        assert "Friday" in intro and name in intro, intro
        assert session["stage"] == "verify_name"
        reply = agent_reply(state, "Yes, speaking", session)["reply"]
        assert session["stage"] == "rsvp" and "Friday" in reply, reply
    wrong = {"name": "Raja", "stage": "verify_name"}
    done = agent_reply(state, "No, this is a wrong number", wrong)
    assert done["done"] and done["result"]["identity_verified"] is False
    print("Personalization tests passed")

if __name__ == "__main__": run()
