

[![AGPLv3 License](https://img.shields.io/badge/License-AGPL%20v3-yellow.svg)](https://opensource.org/licenses/)



# Konstance - AI Watchdog for Centauri

Another Centauri buddy app that i built actually for myself but with AI detection and some other extra added features. Runs on laptop, PC or whatever thing you are running MS Windows on. Alot of vibe code is used while creating the app and so may find some weird things but overall code seemed fine, some things may be broken and cannot guarantee that it works with every Windows or printer firmware. Its my ever first app like this and im not proffessional in these things, as i said. Vibe-code and made it for own use at first, wanted to see how GitHub works and had interest in computer-vision things and decided to upload. 
Konstance was a dog in one low budget, random but funny Estonian early 2000s TV show. 

*Yes, i know some good other dudes in OpenCentauri already dropped Klipper for CC, its really nice, soon with less bugs even better.*


-----
### Main features:

- **Auto-Pause** - trained few CV models in my PC to detect errors while printing and pausing print automatically when error is up.
- **Remote control through Telegram** - Simple few click setup for telegram bot to control your printer anywhere you want.
- **Bed Mesh Manager** - Original firmware lets you to level bed only with 60c bed temp and fixed nozzle temp. Added functionality to store and apply pre-probed meshes in your PC.
- **Gcode visualizer** - Files and Printing tab have visualizer, it could be better but takes alot for time for me, can see where your print is placed, supports and etc on bed before print.
- **Preheat mode** - You can set timer and temperatures on preheat mode, printer will preheat exact minutes you input there, if software cancels the temp it will get them up again.
- **Simple camera filters** - Konstance sided software fiters and for OpenCentauri users easy one click printer driver level changes.(can change exposure and more)
- **Control mechanism** - as all the necssesary controls that original UI has, removed some not important ones for me, easy to add back.
- **Easy to install** -  will run in your everyday Windows machine, different models to make fit for slower and older devices also. No need for a degree in science.
- **Interface lock** - for features you cannot use if you dont run OpenCentauri(Auto-pause DONT need that, but Mesh Manager does)
- **Robust logic** - used original WS commands much as possible.






![Logo](https://github.com/kunnvonkaur/konstance/blob/main/images/header.png?raw=true)

### Some more things 

As i said the app may have some errors, errors may differ in different OS and Firmwares used, may even have some errors that i didnt notice by myself or didnt even think they are too bad bugs to fix right now. As said another thing that i built it actually for myself at first, but saw some people struggle with other 3rd party apps that require Raspberries and things to install to get Auto-pause function to start. Any feedback is always welcome, even if its negative or another "why did you even waste time on that if we have Octo stuff and etc" - simple, i dont like some random connections to unknown networks and i had interest how CV models work, so wanted to give some use to that interest and did Konstance. Konstance runs locally in your home network and needs your PC to run meanwhile prints, for myself its easy as my PC is working kind of 24/7. Idea to upload as simple windows installer came because my friend could not handle installing of it other way. And i know UI may look like from 2001 and give epilepsy with some button colorings.

----


### Auto-Stop features and models:
| Parameter | Type     | Description                |
| :-------- | :------- | :------------------------- |
| Konstance_light_openvino | openVINO | Works best on low-end devices, will save you from mess for sure, but maybe few seconds slower. |
|Konstance_heavy_openvino| openVINO | Abit heavier version of with more "brain" parameters, runs decent especially on intel CPU-s|
|Konstance_heavy_YOLO12m| YOLO12m | Medium Sized YOLO12m model, may ask for more resources, actually original model of openVINO heavy export
|Konstance_Experimental| some YOLO | Using this model when im trying to improve the model, actually alot possible, need more content.

Easy to use, everyone will handle the system of ignore areas if false-poisitve triggers, back of fan grill is triggering pretty often false-positive on current models, i have idea how to fix it but need more CC related error content for that and that takes time for me to produce alone, annodate them and etc, but if there interest then im more motivated to do that, maybe someone can even help me on that. Actually i probably will add some more AI related things to printer, even with this crappy camera i have some pretty cool feature ideas, some useless and some pretty cool really.

- **Interface -** runs real-time inference on the camera stream to identify printing anomalies locally on your PC.
- **Multi-Stage Fault Trigger -** Uses a "Confirmation/Confidence" system to prevent false positives before taking action.
- **Dynamic Model Swapping -** Scans the /models directory to map and load different AI "brains" (OpenVINO folders or PyTorch .pt files) on the fly without restarting.
- **Detection area masking -** Allows users to draw rectangles on the live feed to tell the model to ignore specific areas.(if model hits on some false positives)
- **Software Image Filtering -** Real-time filters (Grayscale, CLAHE, Edge sharpening) to help the AI see better in low-light chambers.
- **Auto-pause -** function will trigger only if its turned on. If turned off, then layer of model is still visible but no automatic pause trigger will happen, you still see anomalies.

(difference between models confidences. Heavy 82% and light 51%)
![App Screenshot](https://github.com/kunnvonkaur/konstance/blob/main/images/difference.png?raw=true)


-------
### Telegram remote control ###

- **Simple few click setup** - everyone will understand the simplified steps, no need to be with some degree to get telegram account connected.
- **All the neccsessary controls, including file upload** - live snapshot, warnings from AI, simple controls, preheating, stop/start/resume/pause prints, file manager.
- **Totally free, no random connections** - as telegram and making bots is free of charge this costs you complete 0. No connections to 3rd party servers.
- **Polling metheod** - Secure, no open ports, no extra hardware needed than your PC and Konstance app.
- **Requires need of Telegram account** - Will run if you have Telegram account.
- **Bot will run inside of Konstance app** - means that the bot is active until Konstance app is running, if you close the app, you kill the bot.
- **Multi-platform** - works on every device that can run Telegram
- **Saves your bot** - no need to run always a new bot, Konstance will run your bot automatically everytime you open the app, if you want that ofcourse, possible to turn off autostart.

Telegram remote controls is a function that is made very simple to setup for people with abit less knowledge of computers. No need to to anything than copy-paste bot token you get from @Botfather in telegram and then click "Auto-caputer" and confirm the user in Konstance, possibility to add more users, turn off warning messages in Konstance and etc. Auto-Pause and Telegram warnings can work at same time or each can run on their own if user decides so. 
Upload function makes it easy to start print of new .gcode file you just recieved, simply send an .gcode to bot and he will accept your gcode and you can start the print.
Please note that OpenCentauri mode functions are off for Telegram remote controls, so it has all the basics, still useful. 

Config file is stored @ \AppData\Local\KonstanceWatchdog as telegram_config.json

![App Screenshot](https://github.com/kunnvonkaur/konstance/blob/main/images/Screenshot%202026-04-10%20211812.png?raw=true)
----------
### Bed Mesh Manager
**WORKS ONLY WITH OC MODE ENABLED (OPENCENTAURI FUNCTION)**

As this printer lacks of function to calibrate bed with other temperature than 60c i needed something better. Manually it worked but was pain in the *ss. So i tried to make something that works abit easier and faster than walking to printer, restarting the printer to load correct mesh and etc. May have some functional bugs and sometimes i have no idea camera feed may freeze after reboot, simply need to click on disconnect and connect again. Reconnect function is written but i dont wanna spam the commands so it may differ abit by boot-up time of the printer. But it has alot of layers of "pre-checks" built in so it wont simply print if the mesh is the wrong one that you did not choose before print. Please note that the meshing works best with pre-heated beds for some time to get constant temperature over the build plate.

- **Mesh harvesting** - pre-probe and store meshes in your PC, unlimited. Simple(as possible imo but im left handed) harvesting logic with configurable nozzle and bed temperatures.
- **Mesh loader** - load harvested mesh into printer, it will require reboot to printer but full sequence is done by the script. Can do that in Gcode viewer before print also.
- **Mesh comparing** - compares your harvested meshes with current active mesh, so you can easly check what mesh printer has active as it intends to change these sometimes.
- **Load default mesh** - before harvesting or loading any mesh Konstance will make sure it always has the backup of the config that you had there pre-konstance use, 1 click rollback
- **Backups** - backups are located in your /board-resource/printer_konstance_backup.cfg and user_printer_konstance_backup.cfg.

_please note that steps of proccess are logged temporary for 3 days in "C:\Users\YOUR_USERNAME\AppData\Local\KonstanceWatchdog" folder. If Gcodes need tuning then temporary gcodes will remain in same folder until the end of printjob_

(Bed Mesh Manager in action)
![App Screenshot](https://github.com/kunnvonkaur/konstance/blob/main/images/Screenshot%202026-04-07%20013115.png?raw=true)



--------------
### Gcode viewer

This is the place where you start prints also, to enter here you will go from File Manager and Printing and open files in printer, also manager allows you to update gcodes from your PC, even bigger files, 20mb takes around 15 seconds, printer needs bigger files to be sent in splits, thats why takes time and my optimization for that is abit worse than original UI.
Anyways, you can see your model placing on plate, support and etc. It sucks abit now as changing line heights and weights by gcode is abit too much resource asking thing or im just bad and cannot get it to work well. I havent focused on that right now also as for me it was enough.

- **Viewer** - can fully go around your object on the viewer and be sure its the correct version of the wanted object. 
- **Viewer "plate"** - 256x256 and will show correct placement of printable object on the bed according to gcode. 
- **Original firmware users** - has same functionality as original one, "time-lapse", "bed-leveling"(60c one) but can start from a layer also to recover sometimes, may be useful.
- **OpenCentauri users** - can use pre-probed meshes for prints here, printer will do sequence automatically and last check is on 1st layer, if no match = auto canceled print.
- **Smart protective layers** - when using "pre-probed" mesh the Konstance will check your gcode and remove calibrate related things from it to prevent mesh overwrite.

_please note that steps of proccess are logged temporary for 3 days in "C:\Users\YOUR_USERNAME\AppData\Local\KonstanceWatchdog" folder. If Gcodes need tuning then temporary gcodes will remain in same folder until the end of printjob__

![App Screenshot](https://github.com/kunnvonkaur/konstance/blob/main/images/viewer_screenshot.png?raw=true)
## Random stuff

[Images](https://https://github.com/kunnvonkaur/konstance/tree/main/images)

