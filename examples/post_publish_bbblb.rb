#!/usr/bin/ruby
# frozen_string_literal: true

require "optimist"
require "java_properties"
require "jwt"
require 'yaml'
require File.expand_path('../../../lib/recordandplayback', __FILE__)

# Logger setup
logger = Logger.new("/var/log/bigbluebutton/post_publish.log", 'weekly' )
logger.level = Logger::INFO
BigBlueButton.logger = logger

# Parse command line arguments
opts = Optimist::options do
  opt :meeting_id, "Meeting id to archive", :type => String
  opt :format, "Playback format name", :type => String
end
meeting_id = opts[:meeting_id]
format_name = opts[:format]
raise('Missing parameter: --meeting-id') unless meeting_id

# Load BBB server secret
props = JavaProperties::Properties.new("/etc/bigbluebutton/bbb-web.properties")
secret = props[:securitySalt]

# Load recording paths
props = YAML.safe_load(File.open('../../core/scripts/bigbluebutton.yml'))
published_dir = props['published_dir'] || raise('Unable to determine published_dir from bigbluebutton.yml')
recording_dir = props['recording_dir'] || raise('Unable to determine recording_dir from bigbluebutton.yml')

# Load meeting metadata
events_xml = "#{recording_dir}/raw/#{meeting_id}/events.xml"
metadata = BigBlueButton::Events.get_meeting_metadata(events_xml)

# Find published recording directories
if format_name then
  format_dirs = ["#{published_dir}/#{format_name}/#{meeting_id}"]
else
  format_dirs = Dir.glob("#{published_dir}/*/#{meeting_id}")
end

format_dirs = format_dirs.filter(File.directory?)
raise("No matching recordings found") unless format_dirs.empty?

# Check if the meeting was started by BBBLB
bbblb_server = metadata['bbblb-server']
bbblb_origin = metadata['bbblb-origin']
unless bbblb_server && bbblb_origin then
  logger.warn("Meeting #{meeting_id} was not started with bbblb, skipping... ")
  exit 0
end

# Generate short-time security token for API request
bbblb_api = "https://#{bbblb_origin}/api/v1/recordings/upload"
bbblb_token = jwt.encode({sub: bbblb_server, exp: Time.now.to_i + 600}, secret, 'HS256', {kid: bbblb_server})

format_dirs.each do |upload_dir|
  # TODO: Create the tar archive first, then try to upload it multiple times
  system(
    {'UPLOAD'=>upload_dir, 'TOKEN'=>bbblb_token, 'API'=>bbblb_api},
  'tar -cz "$UPLOAD" | curl --fail-with-body -X POST -T- -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/x-tar" "$API"'
  ) || raise('Failed to upload recording archive')
end