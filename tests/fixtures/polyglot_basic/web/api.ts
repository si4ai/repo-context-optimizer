/** Typed client for the jobs API. */
import { request } from "./http";

/** One job row as the API returns it. */
export interface Job {
  id: string;
  state?: "queued" | "done";
  retry(n: number): Promise<Job>;
}

export type JobId = string | number;

export enum Color { Red, Green = "g" }

export const MAX_JOBS = 50;

// Fetch one job by id.
export async function getJob(id: JobId, opts: { cache?: boolean } = {}): Promise<Job> {
  const url = `/api/jobs/${id}`; // not a { brace
  return request(url, opts);
}

export const listJobs = async (limit = 10): Promise<Job[]> => {
  return request("/api/jobs?limit=" + limit);
};

function helper(x) { return x / 2; }

export class JobClient extends Base implements Client {
  static VERSION: string = "1";
  private token: string;
  constructor(base: string) { super(); this.base = base; }
  /** Cancel a job. */
  async cancel(id: JobId): Promise<void> { if (id) { await request("/x"); } }
  get count(): number { return 1; }
  #secret() {}
  _internal() {}
}

export { helper as half };
export default JobClient;
