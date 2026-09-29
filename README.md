# canvas-alarms

Canvas's calendar feed has no alarms, and iPhone won't let you add alerts to a subscribed calendar.
This rebuilds the feed with alarms in it (5 PM the day before, 11 PM the night it's due — or an hour
before anything due earlier) and publishes it to GitHub Pages every hour. Subscribe to the Pages URL
once and your phone does the rest.

Feed URLs and output filenames are repository secrets. `extras/*.json` carries items that live only in
a syllabus, and any due times corrected from one.
