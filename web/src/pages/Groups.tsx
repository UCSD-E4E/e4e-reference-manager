import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import type { Group, User } from "../types";

function GroupCard({ group }: { group: Group }) {
  const [members, setMembers] = useState<User[]>([]);
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [err, setErr] = useState("");

  const load = () => api.listMembers(group.id).then(setMembers).catch((e) => setErr(String(e)));
  useEffect(() => {
    if (open) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return;
    try {
      setMembers(await api.addMember(group.id, email.trim()));
      setEmail("");
    } catch (e) {
      setErr(String(e));
    }
  };

  return (
    <div className="card">
      <div className="row">
        <div className="grow">
          <strong>{group.name}</strong> <span className="muted">@{group.slug}</span>
        </div>
        <button className="secondary" type="button" onClick={() => setOpen((o) => !o)}>
          {open ? "Hide" : "Members"}
        </button>
      </div>
      {open && (
        <div style={{ marginTop: "0.5rem" }}>
          {err && <p className="error">{err}</p>}
          {members.map((m) => (
            <div className="item-row" key={m.id}>
              <span className="grow">
                {m.name || m.email} <span className="muted">{m.email}</span>
              </span>
              <button
                className="secondary"
                type="button"
                onClick={() => api.removeMember(group.id, m.id).then(load)}
              >
                Remove
              </button>
            </div>
          ))}
          <form className="row" style={{ marginTop: "0.5rem" }} onSubmit={add}>
            <input
              className="grow"
              placeholder="Add member by email (must have logged in)"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <button type="submit">Add</button>
          </form>
        </div>
      )}
    </div>
  );
}

export default function Groups() {
  const [groups, setGroups] = useState<Group[]>([]);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [err, setErr] = useState("");

  const load = () => api.listGroups().then(setGroups).catch((e) => setErr(String(e)));
  useEffect(() => {
    load();
  }, []);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !slug.trim()) return;
    try {
      await api.createGroup(slug.trim(), name.trim());
      setName("");
      setSlug("");
      load();
    } catch (e) {
      setErr(String(e));
    }
  };

  return (
    <div>
      <h1>Groups</h1>
      {err && <p className="error">{err}</p>}
      <form className="card row" onSubmit={create}>
        <input placeholder="slug" value={slug} onChange={(e) => setSlug(e.target.value)} style={{ width: 140 }} />
        <input className="grow" placeholder="Group name" value={name} onChange={(e) => setName(e.target.value)} />
        <button type="submit">Create</button>
      </form>
      {groups.length === 0 && <p className="muted">No groups yet.</p>}
      {groups.map((g) => (
        <GroupCard key={g.id} group={g} />
      ))}
    </div>
  );
}
