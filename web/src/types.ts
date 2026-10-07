export interface User {
  id: string;
  email: string;
  name: string;
}

export interface Library {
  id: string;
  name: string;
  description: string;
  owner_id: string | null;
  owner_group_id: string | null;
  my_access: "view" | "edit" | "manage" | null;
  created_at: string;
  updated_at: string;
}

export interface Group {
  id: string;
  slug: string;
  name: string;
  description: string;
}

export interface Share {
  id: string;
  group_id: string;
  access_level: "view" | "edit" | "manage";
  created_at: string;
}

export interface Note {
  id: string;
  item_id: string;
  author_id: string | null;
  body: string;
  created_at: string;
  updated_at: string;
}

export interface AuditEvent {
  id: string;
  actor_id: string | null;
  library_id: string | null;
  entity_type: string;
  operation: string;
  summary: string;
  occurred_at: string;
}

export interface IngestResultItem {
  status: "created" | "duplicate";
  item_id: string | null;
  citation_key: string;
  title: string;
  doi: string | null;
}

export interface IngestResult {
  results: IngestResultItem[];
}

export interface Attachment {
  id: string;
  filename: string;
  content_type: string;
  size: number;
  created_at: string;
}

export type ValidationStatus = "verified" | "metadata_mismatch" | "not_found" | "unverifiable";

export interface Validation {
  status: ValidationStatus;
  source: "crossref" | "arxiv" | "none";
  matched_title: string | null;
  title_similarity: number | null;
  notes: string;
  checked_at: string;
}

export interface RefValidation {
  cited_text: string;
  csl: Record<string, unknown>;
  verdict: Validation;
}

export interface PdfValidationResult {
  summary: {
    total: number;
    verified: number;
    metadata_mismatch: number;
    not_found: number;
    unverifiable: number;
  };
  references: RefValidation[];
}

export interface Item {
  id: string;
  library_id: string;
  source_file_id: string | null;
  citation_key: string;
  type: string;
  csl_json: Record<string, unknown>;
  title: string;
  year: number | null;
  doi: string | null;
  version: number;
  validation: Validation | null;
  created_at: string;
  updated_at: string;
  attachments: Attachment[];
}

export interface ItemList {
  total: number;
  items: Item[];
}

export interface ImportDuplicate {
  citation_key: string;
  item_id: string;
  matched_on: "doi" | "title";
}

export interface ImportResult {
  bib_file_id: string;
  filename: string;
  imported: number;
  duplicates: ImportDuplicate[];
  key_collisions: string[];
}

export interface DedupeResult {
  merged: number;
  groups: { kept: string; merged: string[]; matched_on: "doi" | "title" }[];
}

export interface GenerateResult {
  created: number;
  // only for source "ml_tags": items the model just tagged / still without ML tags
  tagged?: number;
  remaining?: number;
}

export type SearchMode = "keyword" | "semantic" | "hybrid";

export interface Collection {
  id: string;
  library_id: string;
  parent_id: string | null;
  name: string;
  created_at: string;
}

export type AutoGroupKind = "field" | "tag" | "search";
export type AutoGroupGenerateSource =
  | "year"
  | "type"
  | "author"
  | "journal"
  | "ml_tags"
  | "manual_tags"
  | "all_tags";

export interface AutoGroup {
  id: string;
  library_id: string;
  name: string;
  kind: AutoGroupKind;
  params: Record<string, unknown>;
  count: number;
  created_at: string;
}

export interface Tag {
  id: string;
  name: string;
  source: "manual" | "ml";
}

export interface SuggestedTags {
  suggestions: string[];
  applied: Tag[];
}

export interface AnnotationRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Annotation {
  id: string;
  attachment_id: string;
  author_id: string | null;
  page: number;
  rects: AnnotationRect[];
  color: string;
  quote: string;
  comment: string;
  created_at: string;
  updated_at: string;
}
