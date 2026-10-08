import { request } from "./api";

export interface ProjectIdentity {
  name: string;
  path: string;
  switching_available: boolean;
}

export const projectApi = {
  current: () => request<ProjectIdentity>("/api/v1/projects/current"),
};
