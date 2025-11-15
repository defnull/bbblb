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
  opt :origin, "Override bbblb-origin metadata and upload to a different bbblb instance", :type => String
  opt :tenant, "Override bbblb-tenant metadata with a different tenant", :type => String
end
meeting_id = opts[:meeting_id]
format_name = opts[:format]
override_origin = opts[:origin]
override_tenant = opts[:tenant]
raise('Missing parameter: --meeting-id') unless meeting_id

# Load BBB server secret and domain
props = JavaProperties::Properties.new("/etc/bigbluebutton/bbb-web.properties")
bbb_secret = props["securitySalt"]
bbb_host = URI.parse(props["bigbluebutton.web.serverURL"]).host

# Load recording paths
props = YAML.safe_load(File.open('../../core/scripts/bigbluebutton.yml'))
published_dir = props['published_dir'] || raise('Unable to determine published_dir from bigbluebutton.yml')
recording_dir = props['recording_dir'] || raise('Unable to determine recording_dir from bigbluebutton.yml')

# Find published recording directories
if format_name then
  format_dirs = ["#{published_dir}/#{format_name}/#{meeting_id}"]
else
  format_dirs = Dir.glob("#{published_dir}/*/#{meeting_id}")
end

format_dirs = format_dirs.filter(File.directory?)
raise("No matching recordings found") unless format_dirs.empty?


# Load meeting metadata
events_xml = "#{recording_dir}/raw/#{meeting_id}/events.xml"
metadata = BigBlueButton::Events.get_meeting_metadata(events_xml)

# Check if the meeting was started by BBBLB
meta_origin = metadata['bbblb-origin'].to_s
meta_origin = override_origin unless override_origin.to_s.empty?
meta_tenant = metadata['bbblb-tenant'].to_s
meta_tenant = override_tenant unless override_tenant.to_s.empty?

# Skip meetings not started via bbblb or overridden with --origin and --tenant cli arguments.
if meta_tenant.empty? || meta_origin.empty? then
  logger.warn("Skipping #{meeting_id}: Missing bbblb-tenant or bbblb-origin metadata."\
  " The meeting was probably not started with bbblb.")
  exit 0
end

# Generate API URL and short-time access token signed with server secret
q = URI.encode_www_form("tenant" => override_tenant) unless override_tenant.to_s.empty?
upload_url = URI::HTTP.new("https", nil, meta_origin, nil, nil, "/bbblb/api/v1/recordings/upload", nil, q, nil)
upload_token = jwt.encode({sub: bbb_host, exp: Time.now.to_i + 600}, bbb_secret, 'HS256', {kid: bbb_host})

format_dirs.each do |upload_dir|
  retries = 10
  success = False
  env = {'UPLOAD'=>upload_dir, 'TOKEN'=>upload_token, 'API'=>upload_url}
  cmd = 'tar -cz "$UPLOAD" | curl -X POST -T- "$API"'\
        ' --fail-with-body' \
        ' -H "Authorization: Bearer $TOKEN"' \
        ' -H "Content-Type: application/x-tar"'
  retries.times do |i|
    success = system(env, cmd)
    break if success
    logger.warn("Command failed, retrying in #{i + 1} seconds... (Attempt #{i + 1}/#{retries})")
    sleep(i+1)
  end
  raise "Failed to upload recording arcive after #{retries} attempts" unless success
end
