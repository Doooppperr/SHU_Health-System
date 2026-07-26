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
      label: `${friend.display_name || "亲友"}（${relation.relation_name || "亲友"}）`,
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

export function withOwnerRequestParams(params = {}, ownerValue = SELF_OWNER_VALUE) {
  const result = { ...params };
  // 页面状态中的 owner_id 是“self”或字符串成员编号，不能直接发给接口。
  // 始终先移除它，再由唯一的转换入口写入真正的数值 owner_id。
  delete result.owner_id;
  return { ...result, ...ownerRequestParams(ownerValue) };
}
