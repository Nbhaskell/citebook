import { useState } from "react";
import { staffApi, type CalendarSlot } from "../api/client";
import { Badge } from "../shared/Badge";
import { SLOT_LABELS, businessClock, businessDate, businessDay } from "../shared/labels";
import { useLoad } from "./useLoad";

function shift(day: string, days: number): string {
  const date = new Date(`${day}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function Slot({ slot }: { slot: CalendarSlot }) {
  const label = SLOT_LABELS[slot.status] ?? { word: slot.status, icon: "question" as const, tone: "neutral" as const };
  return (
    <li className={`slot slot-${slot.status}`}>
      <span className="slot-time">
        {businessClock(slot.starts_at)}–{businessClock(slot.ends_at)}
      </span>
      <Badge word={label.word} icon={label.icon} tone={label.tone} />
      {slot.booking_reference && (
        <span className="slot-detail">
          <span className="ref">{slot.booking_reference}</span> {slot.service}
        </span>
      )}
      {slot.status === "held" && slot.hold_expires_at && (
        <span className="slot-detail">
          Hold expires {businessDay(slot.hold_expires_at)}, {businessClock(slot.hold_expires_at)}
        </span>
      )}
    </li>
  );
}

export function CalendarView() {
  const [from, setFrom] = useState<string | null>(null);
  const { load } = useLoad(() => staffApi.calendar(from, 7), [from], 15000);

  return (
    <section aria-labelledby="calendar-title">
      <div className="view-head">
        <h1 id="calendar-title">Calendar</h1>
        {load.state === "ready" && (
          <div className="button-row">
            <button type="button" onClick={() => setFrom(shift(load.data.first_day, -7))}>
              Previous week
            </button>
            <button type="button" onClick={() => setFrom(null)}>
              This week
            </button>
            <button type="button" onClick={() => setFrom(shift(load.data.first_day, 7))}>
              Next week
            </button>
          </div>
        )}
      </div>
      {load.state === "loading" && <p role="status">Loading the calendar…</p>}
      {load.state === "error" && (
        <p className="error" role="alert">
          {load.message}
        </p>
      )}
      {load.state === "ready" && (
        <>
          <p className="muted" role="status">
            7 days from {businessDay(`${load.data.first_day}T20:00:00Z`)}. Times are business time.
          </p>
          {load.data.technicians.map((tech) => {
            const days = Array.from({ length: load.data.days }, (_, i) => shift(load.data.first_day, i));
            return (
              <section key={tech.technician_id} className="tech" aria-labelledby={`tech-${tech.technician_id}`}>
                <h2 id={`tech-${tech.technician_id}`}>{tech.name}</h2>
                <div className="week">
                  {days.map((day) => {
                    const slots = load.data.slots.filter(
                      (slot) => slot.technician_id === tech.technician_id && businessDate(slot.starts_at) === day,
                    );
                    return (
                      <div key={day} className="day">
                        <h3>{businessDay(`${day}T20:00:00Z`)}</h3>
                        {slots.length === 0 ? (
                          <p className="muted">No visits</p>
                        ) : (
                          <ul className="slots">
                            {slots.map((slot) => (
                              <Slot key={slot.slot_id} slot={slot} />
                            ))}
                          </ul>
                        )}
                      </div>
                    );
                  })}
                </div>
              </section>
            );
          })}
        </>
      )}
    </section>
  );
}
