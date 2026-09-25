import { WorkInProgress } from "./WorkInProgress";

interface ReportPageProps {
  organizationSlug: string;
  repositorySlug: string;
}

export function ReportPage({ organizationSlug, repositorySlug }: ReportPageProps) {
  return <WorkInProgress title={`${organizationSlug} / ${repositorySlug}`} />;
}
