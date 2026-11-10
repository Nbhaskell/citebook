# UI/UX Design Brief: Citebook

Version 0.2 (2026-11-09: review decisions in `docs/DECISIONS.md`). Two surfaces: a customer chat widget on a demo website for the invented plumber, and a staff console. Use `frontend-design` and `ai-ui-patterns` when building. Names and codes from `BACKEND_SCHEMA.md`.

## 1. Design goals

1. **Actions look different from talk.** Anything that changes a booking appears as a card built by code, never as chat text.
2. **Nothing is ambiguous about status.** "Held", "confirmed", "requested" and "cancelled" are distinct words, icons and treatments.
3. **The customer always has a way out:** Cancel on every card, "Talk to a person" always visible.
4. **Staff see everything the bot did,** in order, with the reasons.
5. **Honest demo labelling** everywhere.

## 2. Demo website

A simple one-page site for the invented plumber (from Citedesk's `universe/`): services, area, hours, emergency line prominent at the top. It exists only to host the widget, so keep it plain and believable. Footer: "Demo website. This business, its staff and customers are invented."

## 3. Chat widget

**Launcher:** bottom-right button "Book or ask a question". Opens a panel (full screen below 480 px).

**Header:** business name, "Usually replies in a few seconds", a "Talk to a person" link, and the emergency line in small text with an icon.

**Messages:** customer right, assistant left, staff messages labelled "Team member". Assistant text is plain text with line breaks preserved; no markdown rendering beyond simple lists, never HTML.

**Slot chips:** when the assistant offers times from `check_availability`, the widget shows them as tappable chips built from the tool result ("Thu 12 Nov, arrival 1–3 PM"). Tapping sends the choice as a customer message. Chips are a convenience; they do not confirm anything.

**Quote block:** price lines from `quote_price` rendered by code as a small table: line label, amount (the call-out credit shows as a negative line), total, "Fixed price" or "Estimate: final price confirmed on site", the fixed note that the call-out fee applies if the work is declined on the day, and "Price valid until 5:40 PM".

**Confirmation card** (the most important component):
- Title by action type: "Request this booking", "Move your booking", "Cancel your booking", "Send refund request to our team", "Send request to our team".
- Summary rows from `actions.summary`: service, date and arrival window with day name, address, total (old and new total on a reschedule when they differ), what happens next ("Held until our team confirms, usually within 2 business hours").
- Two buttons: primary **Confirm** and secondary **Cancel**. Clear focus states. Confirm shows a short busy state, then the card becomes a receipt.
- Expired card: greyed, "This expired. Ask again for current times."
- Failed card: "That time was just taken. Here are other options." with chips.
- The assistant's text never says "click confirm to book and pay"; there is no payment.

**Receipt/status chip** after confirmation: reference `CB-7KQ4MX` (copyable), status word and icon: Held (clock), Confirmed (check), Requested (inbox), Cancelled (cross).

**Emergency response:** a distinct alert block (not a chat bubble) with safety steps and the emergency number as a tappable phone link. Followed by "A team member has been alerted."

**Handoff state:** banner "You're now talking with our team. They'll reply here." The input stays enabled.

**Errors:** model slow: "Still working on that…" after 6 seconds; failure: fixed fallback copy and a person offered.

**Footer:** "Demo: all details are invented. Don't enter real information."

## 4. Staff console

**Queue (home):** tabs Needs decision (holds, late cancellations, refunds, exceptions), Handoffs, Expired holds, All recent. Each row: type icon and word, customer name, requested time, amount if any, age, reason code in plain words. Sort oldest first.

**Conversation detail:** three columns on desktop:
- Transcript with messages, cards and receipts as the customer saw them.
- Activity timeline: every tool call with the gate decision as a coloured word and icon (Allowed, Blocked: needs verification, etc.), arguments and result (collapsible JSON), reply checks per assistant message with pass and fail, regenerations shown.
- Decision panel: the pending action's summary, related booking and slot, policy that applies, buttons Approve, Reject (reason required, quick choices), Take over, Close. Refund approval copy: "Approving records your decision. Citebook does not move money."

**Calendar:** week view by technician; slot colours plus labels for open, held, booked, blocked; held slots show expiry time.

**Outbox:** list of drafted notifications with "Draft, not sent" badges. Copy button only.

**Status/settings:** model, provider, KB reachability, synthetic flag, a red banner naming any evaluation ablation in force (widget too); business rules shown read-only (editable values limited to hold time and cancellation window in v1).

## 5. Copy

| Situation | Copy |
|---|---|
| Held | "Your booking is held. Reference CB-7KQ4MX. Our team will confirm it, usually within 2 business hours." |
| Typed confirm | "To make sure it's right, please press Confirm on the card above." |
| Needs verification | "To look up your booking, please give the reference (it starts with CB-) and the last 4 digits of the phone number on the booking." |
| Verification failed (limit) | "I couldn't match those details. I've asked a team member to help." |
| Late cancellation | "Because it's within 24 hours, a team member will review the cancellation and any fee. You'll hear back here." |
| Late reschedule | "Because it's within 24 hours, a team member will review the move and any fee. You'll hear back here." |
| Refund | "I've sent your refund request to our team. A person will review it and reply." |
| Not in knowledge base | "I don't have that information. Would you like me to ask a team member?" |
| Out of area | "We don't currently cover that ZIP code. A team member can suggest options." |

Gate decision labels for staff: Allowed, Blocked: not allowed now, Blocked: needs verification, Blocked: needs contact details, Blocked: invalid details, Blocked: unknown tool, Blocked: too many attempts, Blocked: policy, Blocked: staff only.

## 6. Visual direction

Pick one direction before building and record it in `docs/DECISIONS.md`. Constraints:
- Widget: friendly, trustworthy trade business; strong contrast; the confirmation card should feel like a receipt, not a chat bubble.
- Console: calm, dense, tabular; status always word plus icon plus colour.
- Avoid generic AI gradients, sparkle icons, emoji as UI, and bubbles that make actions look like messages.

## 7. Accessibility

WCAG 2.2 AA. Widget fully keyboard operable; focus moves to a new card when it appears and is announced; live region for new messages (polite) and emergency block (assertive). Buttons at least 44 px on touch. Times include the day name and date in words for screen readers. Respect reduced motion.

## 8. States checklist

Widget: empty welcome, typing, slow, error, chips, quote, card (open, busy, confirmed, cancelled, expired, failed), emergency, handoff, closed. Console: empty queue, loading, error, each action type, decided, expired.

## 9. Open questions

1. Should slot chips also appear in the confirmation card for quick changes? Start without.
2. Should the widget show "the assistant is checking availability" while tools run? Start with a generic typing indicator; test whether naming the step builds trust.
