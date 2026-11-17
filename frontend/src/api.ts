import { z } from "zod";

const assetUrl = z.string().startsWith("/api/");
const jobSchema = z.object({
  id: z.string(),
  status: z.enum(["queued", "processing", "succeeded", "failed"]),
  attempts: z.number().int(),
  max_attempts: z.number().int(),
  error: z.string().nullable(),
});
const personSchema = z.object({
  index: z.number().int().positive(),
  confidence: z.number(),
  bbox: z.object({
    x: z.number(),
    y: z.number(),
    width: z.number(),
    height: z.number(),
  }),
  mask_url: assetUrl,
  isolated_url: assetUrl,
});
const imageSchema = z.object({
  id: z.string(),
  filename: z.string(),
  created_at: z.string(),
  width: z.number().int().positive(),
  height: z.number().int().positive(),
  status: z.enum(["queued", "processing", "ready", "failed"]),
  job: jobSchema,
  original_url: assetUrl,
  preview_url: assetUrl.nullable(),
  mask_url: assetUrl.nullable(),
  isolated_url: assetUrl.nullable(),
  people: z.array(personSchema),
});
const pageSchema = z.object({
  items: z.array(imageSchema),
  next_cursor: z.string().nullable(),
  total: z.number().int().nonnegative(),
});
const errorSchema = z.object({ error: z.object({ message: z.string() }) });
export type ImageRecord = z.infer<typeof imageSchema>;
export type Person = z.infer<typeof personSchema>;

export function errorMessage(error: unknown) {
  return error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
}

async function response(path: string, options?: RequestInit) {
  let result: Response;
  try {
    result = await fetch(path, options);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new Error(
      "Cannot reach the server. Check that the API is running, then try again.",
    );
  }
  if (!result.ok) {
    const error = errorSchema.safeParse(await result.json().catch(() => null));
    throw new Error(
      error.success
        ? error.data.error.message
        : `Request failed (${result.status}). Please try again.`,
    );
  }
  return result;
}

async function json<S extends z.ZodType>(
  path: string,
  schema: S,
  options?: RequestInit,
): Promise<z.output<S>> {
  const data: unknown = await (await response(path, options)).json();
  const parsed = schema.safeParse(data);
  if (!parsed.success)
    throw new Error(
      "The server returned an unexpected response. Refresh to try again.",
    );
  return parsed.data;
}

export const api = {
  gallery: (cursor: string | null, signal: AbortSignal) =>
    json(
      `/api/images?limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
      pageSchema,
      { signal },
    ),
  image: (id: string, signal: AbortSignal) =>
    json(`/api/images/${encodeURIComponent(id)}`, imageSchema, { signal }),
  upload: (file: File) => {
    const body = new FormData();
    body.set("file", file);
    return json("/api/images", imageSchema, { method: "POST", body });
  },
  deleteImage: (id: string) =>
    response(`/api/images/${encodeURIComponent(id)}`, { method: "DELETE" }),
  retry: (id: string) =>
    json(`/api/jobs/${encodeURIComponent(id)}/retry`, jobSchema, {
      method: "POST",
    }),
  selection: async (
    id: string,
    indices: number[],
    view: "mask" | "isolated",
    signal: AbortSignal,
  ) => {
    const result = await response(
      `/api/images/${encodeURIComponent(id)}/cutout?view=${view}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ person_indices: indices }),
        signal,
      },
    );
    if (!result.headers.get("content-type")?.startsWith("image/png"))
      throw new Error("The server did not return a PNG image.");
    return result.blob();
  },
};

export function statusLabel(status: ImageRecord["status"]) {
  return {
    queued: "Queued",
    processing: "Processing",
    ready: "Ready",
    failed: "Failed",
  }[status];
}
