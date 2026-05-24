export interface User {
  id: string;
  email: string;
  name: string;
}

export interface Library {
  id: string;
  name: string;
  description: string;
  owner_id: string;
  created_at: string;
  updated_at: string;
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
