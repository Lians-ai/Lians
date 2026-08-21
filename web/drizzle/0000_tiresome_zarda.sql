CREATE TABLE `app_events` (
	`id` text PRIMARY KEY NOT NULL,
	`app_id` text NOT NULL,
	`actor` text NOT NULL,
	`action` text NOT NULL,
	`value` text NOT NULL,
	`metadata` text NOT NULL,
	`created_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `idx_app_events_app_created` ON `app_events` (`app_id`,`created_at`);--> statement-breakpoint
CREATE TABLE `mini_apps` (
	`id` text PRIMARY KEY NOT NULL,
	`kind` text NOT NULL,
	`title` text NOT NULL,
	`description` text NOT NULL,
	`prompt` text NOT NULL,
	`config` text NOT NULL,
	`remix_of` text,
	`created_at` integer NOT NULL
);
