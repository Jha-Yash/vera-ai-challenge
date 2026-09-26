import os, re, time
from datetime import datetime
from typing import Any, Dict, Optional
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="Vera Challenge Bot", version="1.0.0")
START = time.time()

# In-memory state is explicitly allowed by the challenge as long as the process
# remains alive during the judging window.
contexts: Dict[tuple[str, str], Dict[str, Any]] = {}
conversations: Dict[str, Dict[str, Any]] = {}
sent_suppressions: set[str] = set()

TEAM_NAME = os.getenv("TEAM_NAME", "Yash Kumar Jha")
TEAM_EMAIL = os.getenv("TEAM_EMAIL", "yash.kumar.ug23@nsut.ac.in")
MODEL = os.getenv("MODEL", "deterministic-context-composer")
VERSION = "1.0.0"

class ContextBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: str

class TickBody(BaseModel):
    now: str
    available_triggers: list[str] = Field(default_factory=list)

class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int


def get(scope: str, cid: Optional[str]) -> Optional[Dict[str, Any]]:
    if not cid:
        return None
    x = contexts.get((scope, cid))
    return x.get("payload") if x else None


def owner_name(merchant: Dict[str, Any]) -> str:
    ident = merchant.get("identity", {})
    return ident.get("owner_first_name") or ident.get("name", "there")


def merchant_name(merchant: Dict[str, Any]) -> str:
    return merchant.get("identity", {}).get("name", "your business")


def active_offer(merchant: Dict[str, Any], keywords=()):
    offers = [o for o in merchant.get("offers", []) if o.get("status") == "active"]
    if not offers:
        return None
    for o in offers:
        title = o.get("title", "").lower()
        if any(k.lower() in title for k in keywords):
            return o
    return offers[0]


def category_digest_item(category: Dict[str, Any], item_id: Optional[str] = None):
    for item in category.get("digest", []) or []:
        if item_id and item.get("id") == item_id:
            return item
    return (category.get("digest") or [None])[0]


def fmt_pct(x: Any) -> str:
    try:
        v = float(x)
        if abs(v) <= 1:
            v *= 100
        return f"{v:+.0f}%"
    except Exception:
        return str(x)


def first_sentence(s: str, n=160):
    s = str(s or "").strip()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def compose(trigger: Dict[str, Any], merchant: Dict[str, Any], category: Dict[str, Any], customer: Optional[Dict[str, Any]] = None):
    kind = trigger.get("kind", "")
    p = trigger.get("payload", {}) or {}
    cat = category.get("slug", merchant.get("category_slug", ""))
    owner = owner_name(merchant)
    mname = merchant_name(merchant)
    ident = merchant.get("identity", {})
    locality = ident.get("locality", "")
    perf = merchant.get("performance", {}) or {}
    customer_facing = bool(customer) or trigger.get("scope") == "customer"

    # ---------------- CUSTOMER-FACING ----------------
    if customer_facing and customer:
        cname = customer.get("identity", {}).get("name", "there")
        lang = customer.get("identity", {}).get("language_pref", "english").lower()
        mix = "hi-en" in lang or "hinglish" in lang

        if kind == "recall_due":
            slots = p.get("available_slots", [])
            slot_text = " or ".join(s.get("label", "") for s in slots[:2] if s.get("label"))
            offer = active_offer(merchant, ("cleaning",)) or active_offer(merchant)
            price = offer.get("title") if offer else "your cleaning appointment"
            body = (f"Hi {cname}, {mname} here 🦷 It's been a while since your last visit — "
                    f"your {p.get('service_due','recall')} is due. "
                    f"Apke liye {slot_text} slots available hain. {price}. "
                    f"Reply with the slot that works, or tell us a better time.") if mix else \
                   (f"Hi {cname}, {mname} here 🦷 Your {p.get('service_due','recall')} is due. "
                    f"We have {slot_text}. {price}. Reply with the slot that works, or tell us a better time.")
            return body, "open_ended", "Personalized recall reminder using the customer's relationship state, real slots, active merchant offer, and language preference."

        if kind == "wedding_package_followup":
            offer = active_offer(merchant, ("bridal", "skin", "wedding")) or active_offer(merchant)
            price = offer.get("title") if offer else "a bridal package"
            days = p.get("days_to_wedding")
            body = (f"Hi {cname} 💍 {owner} from {mname} here. {days} days to your wedding — "
                    f"your bridal follow-up window is open after your trial. "
                    f"We can plan the next step around your preferred slot. {price}. "
                    f"Want me to hold a Saturday slot for your first session?")
            return body, "open_ended", "Continues the customer's bridal journey from the prior trial and anchors the message to the wedding date and next-step window."

        if kind == "customer_lapsed_hard":
            focus = p.get("previous_focus", "your previous goal")
            offer = active_offer(merchant, ("trial",)) or active_offer(merchant)
            offer_text = offer.get("title") if offer else "a no-commitment trial"
            body = (f"Hi {cname} 👋 {owner} from {mname} here. It's been about {p.get('days_since_last_visit','a while')} days — "
                    f"no pressure, it happens. You were working on {focus}; we have {offer_text} if you'd like to ease back in. "
                    f"Want me to hold a trial spot for you? No commitment.")
            return body, "open_ended", "No-shame winback that references the customer's previous goal and uses an existing low-friction offer."

        if kind == "trial_followup":
            options = p.get("next_session_options", [])
            slot = options[0].get("label") if options else "a next session"
            body = (f"Hi {cname} 👋 {mname} here. Following up on your trial from {p.get('trial_date','recently')}. "
                    f"We have {slot} available. Want me to reserve it for you?")
            return body, "open_ended", "Trial follow-up uses the real trial date and the supplied next-session option with a single low-friction CTA."

        if kind == "chronic_refill_due":
            meds = ", ".join(p.get("molecule_list", []))
            runout = p.get("stock_runs_out_iso", "")[:10]
            offer = active_offer(merchant, ("delivery", "senior"))
            offer_text = offer.get("title") if offer else "home delivery"
            age = customer.get("identity", {}).get("age_band", "")
            senior = "senior" in age.lower() or "60" in age or "65" in age
            sal = "Namaste — " if senior else f"Hi {cname} — "
            body = (f"{sal}{mname} here. Your regular refill of {meds} is due before {runout}. "
                    f"We can arrange {offer_text.lower()}. Please confirm the refill if the prescription and dosage are unchanged; "
                    f"for any dosage change, please contact your doctor/pharmacist. Reply CONFIRM if you'd like us to arrange it.")
            return body, "open_ended", "Precise refill reminder using only the supplied molecules, due date, merchant offer, and safety boundary around dosage changes."

    # ---------------- MERCHANT-FACING ----------------
    prefix = f"{owner}, " if owner else "Quick heads-up — "

    if kind == "research_digest":
        item = category_digest_item(category, p.get("top_item_id")) or {}
        title = item.get("title", "a new research item")
        summary = item.get("summary", "")
        source = item.get("source", "source provided in the digest")
        n = item.get("trial_n")
        segment = item.get("patient_segment")
        extra = f" {n}-patient" if n else ""
        cohort = ""
        if segment:
            agg = merchant.get("customer_aggregate", {})
            if segment == "high_risk_adults" and agg.get("high_risk_adult_count") is not None:
                cohort = f" It is especially relevant to your {agg['high_risk_adult_count']} high-risk adult patients."
        body = f"{prefix}{title}.{cohort} {first_sentence(summary, 180)} Want me to pull the source and turn the practical takeaway into a ready-to-use customer message? — {source}"
        return body, "open_ended", "External research trigger is tied to a concrete digest item, source, and merchant-specific cohort, with a low-friction artifact offer."

    if kind == "regulation_change":
        item = category_digest_item(category, p.get("top_item_id")) or {}
        deadline = p.get("deadline_iso", "")
        source = item.get("source", "the supplied compliance digest")
        title = item.get("title", "a regulatory change")
        body = f"{prefix}a compliance update is worth acting on: {title}. Deadline: {deadline}. I can turn the supplied guidance into a short checklist for {mname} so nothing gets missed. Want the checklist? — {source}"
        return body, "open_ended", "Regulation trigger is framed around the exact supplied change and deadline, without inventing legal consequences."

    if kind == "perf_dip":
        metric = p.get("metric", "metric")
        delta = fmt_pct(p.get("delta_pct"))
        baseline = p.get("vs_baseline")
        peer = category.get("peer_stats", {}).get(f"avg_{metric}")
        comparison = f" Peer benchmark is {peer}." if peer is not None else ""
        body = f"{prefix}{metric} are {delta} over {p.get('window','the recent window')} (baseline {baseline}).{comparison} Rather than guessing, I can draft one focused change tied to this dip and your current offer. Want me to?"
        return body, "open_ended", "Performance trigger is acknowledged with the supplied delta/window/baseline and a concrete next step."

    if kind == "renewal_due":
        plan = p.get("plan", "current plan")
        amount = p.get("renewal_amount")
        body = f"{prefix}your {plan} renewal is {p.get('days_remaining')} days away{f' at ₹{amount:,}' if amount else ''}. If you want, I can prepare a quick renewal-vs-current-performance summary before you decide."
        return body, "open_ended", "Renewal message states the exact deadline and plan/amount and offers decision support instead of pressure."

    if kind == "festival_upcoming":
        festival = p.get("festival", "the upcoming festival")
        days = p.get("days_until")
        relevant = ", ".join(p.get("category_relevance", []))
        body = f"{prefix}{festival} is on {p.get('date','the supplied date')} ({days} days out). For {cat}, the trigger marks this as relevant. I can turn your existing offer into a simple festival post + WhatsApp copy. Want me to draft it?"
        return body, "open_ended", "Festival trigger is linked to the supplied date, category relevance, and an existing merchant offer workflow."

    if kind == "curious_ask_due":
        offers = [o.get("title") for o in merchant.get("offers", []) if o.get("status") == "active"]
        guess = offers[0] if offers else "your most requested service"
        body = f"Hi {owner}! Quick check — is {guess} still one of the most-asked services at {mname} this week? Tell me what customers are asking for most and I'll turn it into a Google post + a short WhatsApp pricing reply. Takes 5 min."
        return body, "open_ended", "Curiosity trigger asks one low-stakes question and offers to do the content work for the merchant."

    if kind == "winback_eligible":
        body = f"{prefix}{p.get('days_since_expiry')} days since expiry, and {p.get('lapsed_customers_added_since_expiry',0)} lapsed customers have been added since then. Performance is {fmt_pct(p.get('perf_dip_pct'))}. I can draft a simple winback message using your current offer without changing pricing. Want it?"
        return body, "open_ended", "Winback message uses the supplied lapse, performance, and customer-growth signals and proposes a concrete low-effort artifact."

    if kind == "ipl_match_today":
        offer = active_offer(merchant, ("bogo", "pizza")) or active_offer(merchant)
        offer_text = offer.get("title") if offer else "your active offer"
        direction = "skip a match-night dine-in push today" if not p.get("is_weeknight") else "consider a match-night push"
        body = f"Quick heads-up {owner} — {p.get('match')} at {p.get('venue')} tonight, {p.get('match_time_iso','').split('T')[-1][:5]}. Since this is a {'Saturday' if not p.get('is_weeknight') else 'weeknight'} match, I'd {direction}. You already have {offer_text}; I can turn that into a delivery-focused banner + Insta story. Want me to draft both?"
        return body, "open_ended", "Match trigger is interpreted using the supplied venue/time and weekday flag, then connected to the merchant's active offer."

    if kind == "review_theme_emerged":
        theme = p.get("theme", "review theme")
        body = f"{prefix}one review theme is rising: {theme}, {p.get('occurrences_30d')} times in 30 days. The supplied quote says: ‘{p.get('common_quote','') }’. I can turn this into a one-step service-recovery checklist and a customer-facing response draft. Want me to?"
        return body, "open_ended", "Review trigger includes the exact theme, count, and supplied quote, followed by a concrete recovery artifact."

    if kind == "milestone_reached":
        body = f"{prefix}you’re {p.get('milestone_value', '') - p.get('value_now', 0) if isinstance(p.get('milestone_value'), int) and isinstance(p.get('value_now'), int) else 'close'} away from the {p.get('milestone_value')} {p.get('metric')} milestone. You’re at {p.get('value_now')} now. Want me to draft a short milestone post so you can capitalize on it when you cross the line?"
        return body, "open_ended", "Milestone trigger is anchored to the exact current and target values and offers a ready-to-use celebratory asset."

    if kind == "active_planning_intent":
        topic = p.get("intent_topic", "the idea you were planning")
        last = p.get("merchant_last_message", "")
        body = f"{prefix}picking up from your planning note — “{last}”. For {topic.replace('_',' ')}, I can draft a first version using your current offer/pricing and locality details, then you can edit it. Want the draft?"
        return body, "open_ended", "Direct continuation of the merchant's stated planning intent, offering a complete first draft instead of asking them to repeat the brief."

    if kind == "seasonal_perf_dip":
        delta = fmt_pct(p.get("delta_pct"))
        members = merchant.get("customer_aggregate", {}).get("active_members") or merchant.get("customer_aggregate", {}).get("total_active_members")
        season = p.get("season_note", "the supplied seasonal window")
        member_text = f" You have {members} active members, so retention is the cleaner focus." if members else ""
        body = f"{prefix}views are {delta} over {p.get('window','7d')}, and the trigger flags this as expected for {season}.{member_text} Rather than chase acquisition spend during the dip, I can draft a member-retention challenge around your current programs. Want it?"
        return body, "open_ended", "Seasonal dip is reframed using the explicit expected-season flag and supplied delta, with a retention-oriented action."

    if kind == "supply_alert":
        batches = ", ".join(p.get("affected_batches", []))
        body = f"{prefix}urgent supply alert: voluntary recall for {p.get('molecule','the affected medicine')} — batches {batches} — manufacturer {p.get('manufacturer','the supplied manufacturer')}. Please isolate/check those batches against your stock before dispensing. I can draft the customer notification + a pickup/replacement checklist."
        return body, "open_ended", "Supply alert uses the exact molecule, batch numbers, and manufacturer supplied by the trigger, with bounded operational guidance."

    if kind == "category_seasonal":
        trends = ", ".join(p.get("trends", []))
        action = " The trigger recommends a shelf action." if p.get("shelf_action_recommended") else ""
        body = f"{prefix}summer demand is shifting: {trends}.{action} I can turn these exact demand signals into a simple shelf-priority list for {mname}. Want me to draft it?"
        return body, "open_ended", "Seasonal category trigger is summarized using only its supplied demand shifts and recommended action."

    if kind == "gbp_unverified":
        body = f"{prefix}your Google Business Profile is still unverified. The supplied verification path is {p.get('verification_path','the available verification flow')}. I can give you the shortest checklist to complete it and then help you use the listing once verified. Want the checklist?"
        return body, "open_ended", "GBP trigger states the actual verification status and supplied verification path without inventing an uplift guarantee."

    if kind == "cde_opportunity":
        body = f"{prefix}there’s a CDE opportunity in the supplied digest: {p.get('credits')} credits, {p.get('fee')}. I can summarize the session and pull out the one part most relevant to your practice. Want the summary?"
        return body, "open_ended", "CDE trigger uses the supplied credits and fee and offers a concise relevance-filtered summary."

    if kind == "competitor_opened":
        body = f"{prefix}{p.get('competitor_name')} opened {p.get('distance_km')} km away with {p.get('their_offer','a new offer')}. That is useful context, but I wouldn’t copy the price blindly. I can compare their stated offer with your active offer and suggest one positioning angle. Want the comparison?"
        return body, "open_ended", "Competitor message reports the supplied facts and explicitly avoids turning proximity into an unsupported conclusion."

    if kind == "perf_spike":
        body = f"{prefix}{p.get('metric')} are up {fmt_pct(p.get('delta_pct'))} over {p.get('window','7d')} versus a baseline of {p.get('vs_baseline')}. The trigger flags {p.get('likely_driver','the supplied driver')} as likely. Want me to turn what worked into a repeatable post/campaign?"
        return body, "open_ended", "Performance spike cites the exact metric, change, baseline, and supplied likely driver, then proposes a reusable next step."

    if kind == "dormant_with_vera":
        body = f"{prefix}it’s been {p.get('days_since_last_merchant_message')} days since we last spoke; last topic was {p.get('last_topic','your previous topic').replace('_',' ')}. I won’t spam you — if that topic is still relevant, I can pick it up from where we left off."
        return body, "open_ended", "Dormancy trigger explicitly acknowledges the gap and offers a low-pressure continuation instead of a generic nudge."

    # Generic fallback remains context-grounded.
    sig = (merchant.get("signals") or [None])[0]
    body = f"{prefix}quick update for {mname}: this message is triggered by {kind.replace('_',' ')}."
    if sig:
        body += f" One current signal is {sig.replace('_',' ')}."
    body += " I can turn the relevant context into a concrete next step for you — want me to?"
    return body, "open_ended", f"Message is grounded in the supplied {kind} trigger and current merchant context."


def is_auto_reply(msg: str) -> bool:
    s = msg.lower().strip()
    patterns = [
        "thank you for contacting", "thanks for contacting", "thank you for reaching",
        "we have received your message", "will get back to you", "auto reply",
        "office hours", "currently unavailable", "thanks for your message"
    ]
    return any(p in s for p in patterns)


def is_negative(msg: str) -> bool:
    s = msg.lower().strip()
    return bool(re.search(r"\b(no|nah|not interested|don't|do not|stop|later|leave it|no thanks|not now)\b", s))


def is_accept(msg: str) -> bool:
    s = msg.lower().strip()
    return bool(re.search(r"\b(yes|yep|yeah|sure|okay|ok|do it|send|please|interested|go ahead|confirm)\b", s))

@app.get("/v1/healthz")
async def healthz():
    counts = {k: 0 for k in ("category", "merchant", "customer", "trigger")}
    for (scope, _), _v in contexts.items():
        counts[scope] = counts.get(scope, 0) + 1
    return {"status": "ok", "uptime_seconds": int(time.time() - START), "contexts_loaded": counts}

@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": TEAM_NAME,
        "team_members": [TEAM_NAME],
        "model": MODEL,
        "approach": "deterministic 4-context composer with trigger-specific dispatch and stateful replies",
        "contact_email": TEAM_EMAIL,
        "version": VERSION,
        "submitted_at": datetime.utcnow().isoformat() + "Z"
    }

@app.post("/v1/context")
async def push_context(body: ContextBody):
    if body.scope not in {"category", "merchant", "customer", "trigger"}:
        return {"accepted": False, "reason": "invalid_scope", "details": body.scope}
    key = (body.scope, body.context_id)
    cur = contexts.get(key)
    if cur and cur["version"] >= body.version:
        return {"accepted": False, "reason": "stale_version", "current_version": cur["version"]}
    contexts[key] = {"version": body.version, "payload": body.payload}
    return {"accepted": True, "ack_id": f"ack_{body.scope}_{body.context_id}_v{body.version}", "stored_at": datetime.utcnow().isoformat() + "Z"}

@app.post("/v1/tick")
async def tick(body: TickBody):
    actions = []
    for trg_id in body.available_triggers:
        trigger = get("trigger", trg_id)
        if not trigger:
            continue
        merchant_id = trigger.get("merchant_id")
        customer_id = trigger.get("customer_id")
        merchant = get("merchant", merchant_id)
        if not merchant:
            continue
        category = get("category", merchant.get("category_slug"))
        if not category:
            continue
        customer = get("customer", customer_id) if customer_id else None
        suppression = trigger.get("suppression_key") or trg_id
        if suppression in sent_suppressions:
            continue
        body_text, cta, rationale = compose(trigger, merchant, category, customer)
        conv = f"conv_{customer_id or merchant_id}_{trigger.get('kind','event')}_{trg_id}"
        conversations[conv] = {
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "trigger_id": trg_id,
            "trigger": trigger,
            "last_body": body_text,
            "turns": [],
            "send_as": "merchant_on_behalf" if customer_id else "vera"
        }
        sent_suppressions.add(suppression)
        actions.append({
            "conversation_id": conv,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": "merchant_on_behalf" if customer_id else "vera",
            "trigger_id": trg_id,
            "template_name": f"vera_{trigger.get('kind','generic')}_v1",
            "template_params": [merchant_name(merchant), owner_name(merchant), trigger.get("kind")],
            "body": body_text,
            "cta": cta,
            "suppression_key": suppression,
            "rationale": rationale
        })
        if len(actions) >= 20:
            break
    return {"actions": actions}

@app.post("/v1/reply")
async def reply(body: ReplyBody):
    conv = conversations.get(body.conversation_id)
    if not conv:
        return {"action": "end", "rationale": "Unknown conversation; no state is available to continue safely."}
    msg = body.message.strip()
    conv["turns"].append({"role": body.from_role, "body": msg, "turn": body.turn_number})

    # Production pain point: repeated WhatsApp canned auto-replies should terminate quickly.
    if is_auto_reply(msg):
        conv.setdefault("auto_reply_count", 0)
        conv["auto_reply_count"] += 1
        if conv["auto_reply_count"] >= 2:
            return {"action": "end", "rationale": "Detected repeated WhatsApp-style canned auto-replies; ending instead of burning turns."}
        return {"action": "wait", "wait_seconds": 1800, "rationale": "Message resembles a canned WhatsApp auto-reply; backing off rather than asking another qualifying question."}

    if is_negative(msg):
        return {"action": "end", "rationale": "Respecting an explicit decline and ending without repeated persuasion."}

    merchant = get("merchant", conv.get("merchant_id")) or {}
    category = get("category", merchant.get("category_slug")) or {}
    customer = get("customer", conv.get("customer_id")) if conv.get("customer_id") else None
    trigger = conv.get("trigger", {})

    if is_accept(msg):
        if conv.get("customer_id"):
            body_text = "Done — I’ll treat that as your preferred next step. If the timing needs changing, just tell us what works."
        else:
            body_text = f"Absolutely, {owner_name(merchant)}. I’ll keep it focused on {trigger.get('kind','this item').replace('_',' ')} and use the context you already shared. What I can prepare first is the ready-to-use draft, so you only need to review it."
        conv["last_body"] = body_text
        return {"action": "send", "body": body_text, "cta": "open_ended", "rationale": "Accepted the merchant/customer intent and moved directly to the promised next artifact without re-qualifying."}

    # Handle a useful question without hallucinating new facts.
    if "?" in msg or re.search(r"\b(what|how|which|when|where|price|cost|details)\b", msg.lower()):
        last = conv.get("last_body", "")
        body_text = f"Good question. I’ll stick to the details already provided for this {trigger.get('kind','conversation').replace('_',' ')} and avoid making up anything not in your data. If you tell me which part you want to act on first, I’ll draft that piece."
        return {"action": "send", "body": body_text, "cta": "open_ended", "rationale": "Answers cautiously from available context and asks for one concrete next step rather than fabricating details."}

    return {"action": "wait", "wait_seconds": 900, "rationale": "No clear action intent in the reply; pausing briefly instead of over-messaging."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
