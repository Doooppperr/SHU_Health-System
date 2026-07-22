export const SELF_OWNER_VALUE = "self";

export function buildHealthOwnerOptions(payload = {}, currentUser = {}) {
  const options = [
    {
      value: SELF_OWNER_VALUE,
      ownerId: null,
      label: "本人",
    },
  ];
  const seen = new Set();

  for (const relation of payload.outgoing || []) {
    const friend = relation?.friend_user;
    if (!relation?.auth_status || !friend?.id || seen.has(friend.id)) {
      continue;
    }
    seen.add(friend.id);
    options.push({
      value: String(friend.id),
      ownerId: friend.id,
      label: `${friend.username || "亲友"}（${relation.relation_name || "亲友"}）`,
    });
  }

  return options;
}

export function ownerRequestParams(ownerValue) {
  if (!ownerValue || ownerValue === SELF_OWNER_VALUE) {
    return {};
  }
  return { owner_id: Number(ownerValue) };
}
