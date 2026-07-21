import { useQuery } from "@tanstack/react-query";

import { getSession } from "../api/client";

export const sessionQueryKey = ["session"] as const;

/** 查询服务端可信 Session，并让 API Client 同步内存态 CSRF Token。 */
export function useSessionQuery() {
  return useQuery({ queryKey: sessionQueryKey, queryFn: getSession });
}
