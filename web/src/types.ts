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
  created_at: string;
  updated_at: string;
  attachments: Attachment[];
}

export interface ItemList {
  total: number;
  items: Item[];
}

export interface ImportResult {
  bib_file_id: string;
  filename: string;
  imported: number;
  key_collisions: string[];
}

export type SearchMode = "keyword" | "semantic" | "hybrid";

export interface Collection {
  id: string;
  library_id: string;
  parent_id: string | null;
  name: string;
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
