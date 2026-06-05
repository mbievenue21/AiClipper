ALTER TABLE `videos` ADD `scene_cuts_json` text;--> statement-breakpoint
CREATE TABLE `chat_features` (
	`id` text PRIMARY KEY NOT NULL,
	`video_id` text NOT NULL,
	`density_json` text NOT NULL,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	FOREIGN KEY (`video_id`) REFERENCES `videos`(`id`) ON UPDATE no action ON DELETE cascade
);--> statement-breakpoint
CREATE UNIQUE INDEX `chat_features_video_id_unique` ON `chat_features` (`video_id`);--> statement-breakpoint
ALTER TABLE `clips` ADD `trim_start_seconds` real;--> statement-breakpoint
ALTER TABLE `clips` ADD `trim_end_seconds` real;--> statement-breakpoint
ALTER TABLE `clips` ADD `caption_segments_json` text;--> statement-breakpoint
ALTER TABLE `clips` ADD `parent_clip_id` text;--> statement-breakpoint
ALTER TABLE `clips` ADD `version_label` text;--> statement-breakpoint
ALTER TABLE `clips` ADD `is_active` integer DEFAULT true NOT NULL;--> statement-breakpoint
ALTER TABLE `clips` ADD `superseded_at` integer;
