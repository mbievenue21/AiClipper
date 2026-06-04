ALTER TABLE `clips` ADD `captioned_file_path` text;--> statement-breakpoint
ALTER TABLE `clips` ADD `width_px` integer;--> statement-breakpoint
ALTER TABLE `clips` ADD `height_px` integer;--> statement-breakpoint
ALTER TABLE `clips` ADD `status` text DEFAULT 'rendering' NOT NULL;--> statement-breakpoint
ALTER TABLE `clips` ADD `dominant_color` text;--> statement-breakpoint
ALTER TABLE `clips` ADD `caption_style_json` text;--> statement-breakpoint
ALTER TABLE `clips` ADD `error_message` text;--> statement-breakpoint
ALTER TABLE `clips` ADD `updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL;--> statement-breakpoint
ALTER TABLE `scheduled_uploads` ADD `platform` text NOT NULL;--> statement-breakpoint
ALTER TABLE `scheduled_uploads` ADD `visibility` text DEFAULT 'private' NOT NULL;--> statement-breakpoint
ALTER TABLE `scheduled_uploads` ADD `timezone` text DEFAULT 'America/Chicago' NOT NULL;--> statement-breakpoint
ALTER TABLE `scheduled_uploads` ADD `attempts` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
CREATE INDEX `scheduled_uploads_clip_idx` ON `scheduled_uploads` (`clip_id`);