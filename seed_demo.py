"""
Seed the database with sample tickets so a fresh install has something to look
at -- the triage queue, the dedup link, follow-ups, and every workflow state
from new through ready-for-verification to verified.

    python seed_demo.py            # add samples (refuses if tickets exist)
    python seed_demo.py --force    # add them anyway

Demo data only: the submitter names are plain strings, no accounts are created
and no passwords are set, so this can't hand anyone a way in.
"""
import sys

import db
import migrate_db
import taxonomy

RESOLUTION_STATUS = {r["id"]: r["status"] for r in taxonomy.load()["resolutions"]}

SAMPLES = [
    {
        "toolkit": "rad-agent-toolkit",
        "categories": "slow_result",
        "knowledge_scope": "rad",
        "knowledge_sources": "manual",
        "operations": "lookup",
        "severity": "high",
        "title": "manual_search takes over a minute for ETX-2 QoS questions",
        "prompt": "what is the QoS hierarchy on ETX-2 and how many queues per port",
        "description": (
            "Asked for the ETX-2 QoS hierarchy and the agent sat on manual_search for "
            "about 90 seconds before answering. Same question a second time was just as "
            "slow, so it isn't a cold cache. Short questions come back fine."
        ),
        "expected_behavior": "An answer in a few seconds, like other manual lookups.",
        "actual_behavior": "~90s of nothing, then a correct but very late answer.",
        "transcript": (
            "User: what is the QoS hierarchy on ETX-2 and how many queues per port\n"
            "GitHub Copilot: (manual_search running...)\n"
            "GitHub Copilot: (still running...)\n"
            "GitHub Copilot: ETX-2 supports 8 queues per port with strict priority and WFQ...\n"
            "User: that took over a minute, is that expected?"
        ),
        "toolkit_version": "rad-agent-toolkit 1.4.2\nrad-mcp 0.9.1\nrad-cli-reference 2026.07.30",
        "submitter_username": "dana",
        "submitter_email": "dana@example.com",
        "status_after": "triaged",
        "comments": [
            ("dana", "Dana", "Happens on Megaplex manual questions too, not just ETX-2.", False),
        ],
    },
    {
        "toolkit": "rad-agent-toolkit",
        "categories": "slow_result",
        "knowledge_scope": "rad",
        "knowledge_sources": "mib",
        "operations": "device_read",
        "severity": "normal",
        "title": "SNMP walk on Megaplex hangs for minutes before timing out",
        "prompt": "walk ifTable on the megaplex in the lab",
        "description": (
            "snmp_walk against a Megaplex-4100 appears to hang. After roughly four "
            "minutes it returns a timeout error. A walk of the same subtree from the "
            "command line finishes in a couple of seconds."
        ),
        "expected_behavior": "Either results quickly, or a fast, clear timeout.",
        "actual_behavior": "Four minutes of silence, then 'request timed out'.",
        "transcript": (
            "User: walk ifTable on the megaplex in the lab\n"
            "GitHub Copilot: (snmp_walk running...)\n"
            "GitHub Copilot: The SNMP request timed out.\n"
            "User: net-snmp walks the same OID in 2 seconds from my laptop"
        ),
        "toolkit_version": "rad-agent-toolkit 1.4.2\nrad-mcp 0.9.1",
        "submitter_username": "omer",
        "submitter_email": "omer@example.com",
        "status_after": "new",
    },
    {
        "toolkit": "rad-agent-toolkit",
        "categories": "wrong_result,many_retries",
        "knowledge_scope": "rad",
        "knowledge_sources": "manual,cli",
        "operations": "lookup",
        "severity": "high",
        "title": "Agent invented a `set qos policer` command that doesn't exist",
        "prompt": "how do I rate limit a service to 100M on etx-2i",
        "description": (
            "Asked how to rate-limit a service on an ETX-2i. The agent gave a confident, "
            "well-formatted command that the device rejects outright. It looks plausible, "
            "which is what makes it dangerous -- I nearly pasted it into a live unit."
        ),
        "expected_behavior": "The real policer syntax, or an admission that it isn't known.",
        "actual_behavior": "An invented command; the device answers 'Invalid command'.",
        "transcript": (
            "User: how do I rate limit a service to 100M on etx-2i\n"
            "GitHub Copilot: Use: set qos policer service-1 cir 100000 cbs 64\n"
            "User: the unit says Invalid command\n"
            "GitHub Copilot: Apologies, try: set qos policer-profile ...\n"
            "User: that one doesn't exist either"
        ),
        "suggestion": (
            "The CLI reference has no policer entries for ETX-2 at all. Index them, and "
            "make the agent say 'not in the reference' instead of inventing syntax when "
            "a CLI lookup comes back empty."
        ),
        "toolkit_version": "rad-agent-toolkit 1.4.2\nrad-cli-reference 2026.07.30",
        "submitter_username": "yael",
        "submitter_email": "yael@example.com",
        "status_after": "resolved",
        "resolution": "fixed",
        "verified": True,
        "fixed_in_versions": (
            "rad-agent-toolkit 1.5.0\nrad-mcp 0.9.3\nrad-cli-reference 2026.08.21"
        ),
        "resolution_note": (
            "The ETX-2 policer syntax was missing from the CLI reference, so the model "
            "filled the gap. Added the real commands and a guard against answering CLI "
            "questions from memory when the reference has no entry."
        ),
        "fixed_answer": (
            "On ETX-2, rate limiting a service is done with a bandwidth profile bound to "
            "the flow, not a `policer` command (which doesn't exist on this platform):\n\n"
            "    configure qos\n"
            "      bandwidth-profile ingress 1 cir 100000 cbs 64 eir 0\n"
            "      exit\n"
            "    configure flows flow 1\n"
            "      bandwidth-profile ingress 1\n"
            "      exit\n\n"
            "CIR is in kbps, so 100M is cir 100000. Verify with "
            "`show qos bandwidth-profile ingress 1`."
        ),
        "comments": [
            ("yael", "Yael", "Reproduced on two different ETX-2i units, both on 6.8.", False),
            ("admin", "admin", "Confirmed -- the CLI reference has no policer entries at all.", True),
        ],
    },
    {
        "toolkit": "rad-agent-toolkit",
        "categories": "wrong_result",
        "knowledge_scope": "rad",
        "knowledge_sources": "mib",
        "operations": "lookup,device_read",
        "severity": "normal",
        "title": "MIB lookup returned the wrong OID for interface counters",
        "prompt": "which oid do I poll for input errors on etx-203",
        "description": (
            "Asked for the OID to poll interface error counters. Got an OID from a "
            "different MIB that returns nothing on our units."
        ),
        "expected_behavior": "The correct OID from the RAD private MIB.",
        "actual_behavior": "A standard-MIB OID our firmware doesn't populate.",
        "transcript": (
            "User: which oid do I poll for input errors on etx-203\n"
            "GitHub Copilot: Use 1.3.6.1.2.1.2.2.1.14 (ifInErrors)\n"
            "User: that returns 0 always, the counters are in the RAD private tree"
        ),
        "toolkit_version": "rad-agent-toolkit 1.4.2\nrad-mcp 0.9.1",
        "submitter_username": "omer",
        "submitter_email": "omer@example.com",
        "status_after": "resolved",
        "resolution": "fixed",
        "fixed_in_versions": "rad-agent-toolkit 1.5.0\nrad-mcp 0.9.3\nrad-snmp-operations 2026.08.22",
        "fixed_answer": (
            "Input errors on ETX-203 live in the RAD private tree, not ifTable:\n\n"
            "    1.3.6.1.4.1.164.3.1.5.2.1.9  (radEthPortInErrors)\n\n"
            "ifInErrors (1.3.6.1.2.1.2.2.1.14) is present but not populated on this "
            "platform, which is why it always reads 0."
        ),
        "resolution_note": "Added the RAD private counter OIDs to the MIB index.",
    },
    {
        "toolkit": "radview-ai-toolkit",
        "categories": "wrong_result,many_retries",
        "knowledge_scope": "rad",
        "knowledge_sources": "cli,skills",
        "operations": "lookup",
        "severity": "normal",
        "title": "Asked for a SecFlow config, got an ETX-2 example instead",
        "prompt": "show me how to configure a bridge on secflow-2",
        "description": (
            "I said SecFlow-2 explicitly, twice. Both answers were ETX-2 configuration "
            "with ETX context paths. The content was fine, just for the wrong product."
        ),
        "expected_behavior": "SecFlow-2 syntax, or a note that it isn't covered.",
        "actual_behavior": "ETX-2 syntax presented as if it were the SecFlow answer.",
        "transcript": (
            "User: show me how to configure a bridge on secflow-2\n"
            "GitHub Copilot: On ETX-2, enter configure bridge...\n"
            "User: I asked about SecFlow-2, not ETX\n"
            "GitHub Copilot: Certainly. On ETX-2 the bridge context is..."
        ),
        "toolkit_version": "rad-agent-toolkit 1.4.2",
        "submitter_username": "dana",
        "submitter_email": "dana@example.com",
        "status_after": "triaged",
    },
    {
        "toolkit": "rad-agent-toolkit",
        "categories": "wrong_result",
        "knowledge_scope": "rad",
        "knowledge_sources": "skills",
        "operations": "device_read,device_write",
        "severity": "high",
        "title": "Asked to stage a change only, agent committed it to the device",
        "prompt": "stage this vlan change on the lab etx, don't commit yet",
        "description": (
            "I asked for the config to be staged so I could review the preview first. "
            "The agent staged it and then committed without waiting for me to confirm. "
            "Nothing broke, but on a production unit this would not have been fine."
        ),
        "expected_behavior": "Stage, show the preview, and stop until I confirm.",
        "actual_behavior": "Staged and committed in one go.",
        "transcript": (
            "User: stage this vlan change on the lab etx, don't commit yet\n"
            "GitHub Copilot: Staged as stage_id 41. Committing now...\n"
            "GitHub Copilot: Commit successful, running-config backed up.\n"
            "User: I explicitly said do not commit"
        ),
        "toolkit_version": "rad-agent-toolkit 1.4.2\nrad-mcp 0.9.1",
        "submitter_username": "admin",
        "submitter_email": "",
        "status_after": "triaged",
        "comments": [
            ("admin", "admin", "Raising severity -- this is the one class of bug that can touch live kit.", False),
        ],
    },
    {
        "toolkit": "pikachu-ai-toolkit",
        "categories": "wrong_result",
        "knowledge_scope": "rad,market",
        "knowledge_sources": "datasheet",
        "operations": "lookup",
        "severity": "normal",
        "title": "No answer at all for a MiNID power budget question",
        "prompt": "what's the optical power budget for the minid sfp variant",
        "description": (
            "Asked for the optical power budget of a MiNID SFP variant. The agent "
            "searched, found nothing, and stopped without saying where to look next."
        ),
        "expected_behavior": "The figure from the datasheet, or a clear 'not covered, try X'.",
        "actual_behavior": "'I could not find information about that.' and nothing more.",
        "transcript": (
            "User: what's the optical power budget for the minid sfp variant\n"
            "GitHub Copilot: I could not find information about that."
        ),
        "suggestion": "Index the MiNID datasheets - the power budget table is in them.",
        "toolkit_version": "rad-agent-toolkit 1.4.2\nrad-reference-knowledge 2026.07.30",
        "submitter_username": "yael",
        "submitter_email": "yael@example.com",
        "status_after": "new",
    },
    {
        "toolkit": "synergy-ccm-mcp",
        "categories": "wrong_result",
        "knowledge_scope": "rad,market",
        "knowledge_sources": "manual,datasheet,release_notes",
        "operations": "lookup",
        "severity": "low",
        "title": "Nothing available on ETX-2V virtualized deployment",
        "prompt": "how many vcpus does etx-2v need on kvm",
        "description": (
            "Asked about resource requirements for running ETX-2V on a KVM host. The "
            "agent had no material to work from and said so."
        ),
        "expected_behavior": "vCPU/RAM guidance from the ETX-2V documentation.",
        "actual_behavior": "No source found; the question went unanswered.",
        "transcript": (
            "User: how many vcpus does etx-2v need on kvm\n"
            "GitHub Copilot: I don't have documentation covering ETX-2V deployment."
        ),
        "toolkit_version": "rad-agent-toolkit 1.4.2",
        "submitter_username": "dana",
        "submitter_email": "dana@example.com",
        "status_after": "resolved",
        "resolution": "known_issue",
        "resolution_note": (
            "The ETX-2V deployment guide is under NDA and can't be indexed. Pointing "
            "people at the product manager instead."
        ),
    },
    {
        # Deliberately close to the first ticket: this one should land as a duplicate.
        "toolkit": "rad-agent-toolkit",
        "categories": "slow_result",
        "knowledge_scope": "rad",
        "knowledge_sources": "manual",
        "operations": "lookup",
        "severity": "high",
        "title": "manual_search takes over a minute for ETX-2 QoS questions",
        "prompt": "what is the QoS hierarchy on ETX-2 and how many queues per port",
        "description": (
            "Asked for the ETX-2 QoS hierarchy and the agent sat on manual_search for "
            "about 95 seconds before answering. Same question a second time was just as "
            "slow, so it isn't a cold cache. Short questions come back fine."
        ),
        "expected_behavior": "An answer in a few seconds, like other manual lookups.",
        "actual_behavior": "Well over a minute of nothing, then a correct answer.",
        "transcript": (
            "User: explain the etx-2 qos hierarchy, how many queues per port\n"
            "GitHub Copilot: (long pause)\n"
            "GitHub Copilot: 8 queues per port, strict priority plus WFQ..."
        ),
        "toolkit_version": "rad-agent-toolkit 1.4.2\nrad-mcp 0.9.1",
        "submitter_username": "omer",
        "submitter_email": "omer@example.com",
        "status_after": "new",
    },
]


def seed(force: bool = False) -> int:
    migrate_db.migrate()
    existing = db.list_tickets()
    if existing and not force:
        print(f"{len(existing)} ticket(s) already present. Re-run with --force to add samples.")
        return 0

    created = 0
    for sample in SAMPLES:
        data = {k: v for k, v in sample.items()
                if k not in ("status_after", "comments", "resolution", "fixed_in_versions",
                             "fixed_answer", "resolution_note", "verified")}
        ticket_id = db.create_ticket(data)
        ticket = db.get_ticket(ticket_id)
        created += 1

        for author, display, body, admin_note in sample.get("comments", []):
            db.add_comment(ticket_id, author, display, body, is_admin_note=admin_note)

        # A ticket the dedup caught keeps its duplicate status.
        if ticket["status"] == "duplicate":
            print(f"  #{ticket_id} linked as a duplicate of #{ticket['duplicate_of']}")
            continue

        wanted = sample.get("status_after", "new")
        if wanted == "resolved":
            db.resolve_ticket(
                ticket_id,
                sample["resolution"],
                RESOLUTION_STATUS.get(sample["resolution"], "known_issue"),
                resolved_by="admin",
                fixed_in_versions=sample.get("fixed_in_versions", ""),
                fixed_answer=sample.get("fixed_answer", ""),
                note=sample.get("resolution_note", ""),
            )
            state = RESOLUTION_STATUS.get(sample["resolution"])
            if sample.get("verified"):
                db.verify_ticket(ticket_id, sample["submitter_username"])
                state = "verified by " + sample["submitter_username"]
            print(f"  #{ticket_id} {state}: {ticket['title'][:44]}")
        elif wanted != "new":
            db.update_status(ticket_id, wanted)
            print(f"  #{ticket_id} {wanted}: {ticket['title'][:50]}")
        else:
            print(f"  #{ticket_id} new: {ticket['title'][:50]}")

    print(f"\nSeeded {created} tickets.")
    return created


if __name__ == "__main__":
    seed(force="--force" in sys.argv)

