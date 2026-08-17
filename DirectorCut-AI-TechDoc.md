# TECHNICAL DESIGN DOCUMENT (TDD)
## Project Name: DirectorCut-AI (Automated Rough-Cut Editor)
## Date: August 2026

---

## 1. Executive Summary & Vision
DirectorCut-AI is a local-first, cloud-intelligent automation tool designed to transform raw video/audio production files into narrative-driven, entertainment-ready rough cuts. 

Unlike traditional "silence-cutter" tools that rely purely on transcription text matching, DirectorCut-AI treats editing as a multi-pass structural task. It identifies thematic setups, comedic irony, structural callbacks, and emotional spikes using **Gemini 3.7 Flash**, compiling these decisions directly into a timeline file (**FCPXML / OpenTimelineIO**) native to professional Non-Linear Editors (NLEs) like DaVinci Resolve.

### Core Key Performance Indicators (KPIs)
*   **Context Optimization**: Fits up to 2 hours of source assets seamlessly into Gemini’s 1,000,000 token context window.
*   **Economic Viability**: Costs less than \$1.50 per final rough cut in API usage fees.
*   **Structural Fidelity**: Delivers an NLE timeline output requiring only dynamic pacing adjustments and final sound treatment from the user.

---

## 2. System Architecture Overview

The system utilizes an asymmetrical **Local-Process / Cloud-Brain** topology. Processing raw 4K 60fps game footage natively in the cloud is technically and economically impossible due to network bandwidth limitations and API context ceilings. 

```
[ Local Client App ]
   │
   ├── 1. Ingestion Engine ──────> [ Full-Res Assets & Scripts Directory ]
   │
   ├── 2. Proxy Pipeline ────────> [ FFmpeg Engine: Audio Stripping & 1-FPS Downsampling ]
   │
   ├── 3. API Choreographer ─────> [ Orchestrates Orchestration Pipeline with Gemini Cloud ]
   │
   └── 4. Conformer/Compiler ────> [ Receives Final Edit Blueprint JSON -> Generates FCPXML ]
                                          ▲
                                          │ (Edit Blueprint JSON)
[ Gemini Cloud Services ]                 │
   │                                      │
   ├── Pass 1: Narrative Engine ──────────┤ (Generates Voice Over / Script Node Sequence)
   ├── Pass 2: Visual Event Logger ───────┤ (Generates Visual State Vector Array)
   └── Pass 3: The Assembly Director ─────┘ (Cross-references Maps to Produce Cut List)
```

---

## 3. Detailed Component Architecture

### 3.1 Local Pre-Processing (The "Proxy Engine")
To optimize API network performance and context storage constraints, raw video assets must undergo significant local downsampling before cloud staging.

```
+--------------------------+     FFmpeg (Scale, Framerate)     +---------------------------+
| Raw Gameplay 4K/60 File  | ────────────────────────────────> | 360p, 1-FPS Visual Proxy  |
| Length: 90 Minutes       |                                   | Token Count: ~1.4M -> 72k |
+--------------------------+                                   +---------------------------+
             │
             │ FFmpeg (-vn -codec:a libmp3lame)
             ▼
+--------------------------+
| Compact Audio Stream File|
+--------------------------+
```

*   **Visual Optimization Mapping Matrix**:
    *   *Raw Input*: 90 mins @ 4K, 60fps = Token Exhaustion / Ingestion Time Out.
    *   *Proxy Target*: 360p resolution, converted to a strict 1-FPS variable/constant keyframe format.
    *   *Token Math Result*: 90 mins $	imes$ 60 secs $	imes$ 1 frame $	imes$ 263 video tokens = **1,420,200 tokens**. By further optimizing the visual track down to a targeted frame interval when necessary, the asset easily fits within the standard context target.

### 3.2 The Multi-Pass Cloud AI Pipeline

#### Pass 1: Narrative Mapping Engine
*   **Inputs**: Script text file, raw voiceover audio track.
*   **Function**: Maps the spoken line array directly to timestamps. Annotates sentences based on intent metadata:
    *   `EXPOSITION`: Flat informational setups.
    *   `COMEDIC_SET`: Setup for a joke or punchline.
    *   `EMOTIONAL_SPIKE`: Yelling, cheering, intense disappointment.
    *   `CALLBACK_REQ`: Explicit queries looking for evidence ("When did that happen?!").

#### Pass 2: Visual Structural Logger
*   **Inputs**: Low-res, 1-FPS gameplay visual track proxy.
*   **Function**: Computes structural state markers purely from visual context:
    *   `STATE_UI`: Detecting inventory menus, vendor windows, and skill selection lists.
    *   `STATE_ACTION`: Tracking intense movement loops, kill feeds, or combo meters.
    *   `STATE_DEATH`: Sudden UI screens indicating a reset or fail state.
    *   `STATE_TUTORIAL`: Popup informational windows on screen.

#### Pass 3: The Assembly Pass (The "Director")
*   **Inputs**: The Narrative Node Matrix JSON + Visual State Vector JSON.
*   **Task**: Execute structural editing instructions using targeted logical scripts.

```
       [ Narrative Map Node ]                            [ Visual State Log Matrix ]
       "Why didn't they tell me?!" ───────────────────┐  Visual Event: Tutorial Window
       Tag: [CALLBACK_REQ]                            │  Timestamp: 00:14:22
                                                      ▼
                                            [ ASSEMBLY LOGIC MATRIX ]
                                         Match Event to Callback Sequence
                                                      │
                                                      ▼
                                           [ OUTPUT TIMELINE EDIT ]
                                        Insert Visual Context at Exact 
                                           Spoken Timing Instants
```

---

## 4. Database & Internal JSON Schema Specifications

The internal runtime representation uses a standard structured JSON interface to pass data smoothly across the three compiler tiers.

### 4.1 Narrative Node Output Schema
```json
{
  "narrative_nodes": [
    {
      "node_id": "N_001",
      "time_start": "00:01:12.500",
      "time_end": "00:01:15.200",
      "spoken_text": "I bet I can beat this boss first try.",
      "thematic_tag": "COMEDIC_SET",
      "energy_level": "HIGH"
    }
  ]
}
```

### 4.2 Visual State Event Output Schema
```json
{
  "visual_events": [
    {
      "event_id": "V_204",
      "time_start": "00:01:16.800",
      "time_end": "00:01:19.100",
      "classification": "STATE_DEATH",
      "visual_description": "Player character gets squashed by a boulder, showing immediate Game Over overlay Screen."
    }
  ]
}
```

### 4.3 Final Unified Assembly Blueprint Output Schema
```json
{
  "timeline_meta": {
    "project_name": "LetsPlay_Episode_1_RoughCut",
    "target_fps": 60.0
  },
  "cuts": [
    {
      "track": "V1_A1_PRIMARY",
      "source_file": "voiceover_raw.mp3",
      "timeline_in": "00:00:00.000",
      "timeline_out": "00:00:02.700",
      "source_in": "00:01:12.500",
      "source_out": "00:01:15.200"
    },
    {
      "track": "V2_OVERLAY_BROLL",
      "source_file": "gameplay_capture.mp4",
      "timeline_in": "00:00:02.700",
      "timeline_out": "00:00:05.000",
      "source_in": "00:01:16.800",
      "source_out": "00:01:19.100"
    }
  ]
}
```

---

## 5. Timeline Formatting & NLE Interoperability

To generate native timelines directly inside **DaVinci Resolve**, the system converts the final `Assembly Blueprint JSON` into standard Apple Final Cut Pro XML (**FCPXML**), using standard sequence frame components.

### Implementation Blueprint for FCPXML Construction
The output script wraps timeline track allocations inside clean XML tracking segments:
1.  `<resources>`: Declares tracking definitions for all high-res master source file strings.
2.  `<library>`: Defines structural organizational asset tags.
3.  `<sequence>`: Orchestrates the target frame rates and maps specific clips to their visual tracks (`<track name="V1">`) using microsecond frame durations.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.8">
    <resources>
        <format id="r1" name="FFVideoFormat1080p60" frameDuration="100/6000s"/>
        <asset id="a1" name="gameplay" src="file:///C:/Media/gameplay_capture.mp4" start="0s" duration="5400s"/>
    </resources>
    <library>
        <event name="DirectorCut_Output">
            <project name="RoughCut_V1">
                <sequence duration="900s" format="r1" tcStart="0s">
                    <spine>
                        <!-- Local tracks map frame locations accurately here -->
                        <video ref="a1" offset="0s" name="Gameplay Cut" start="442800/6000s" duration="18000/6000s"/>
                    </spine>
                </sequence>
            </project>
        </event>
    </library>
</fcpxml>
```

---

## 6. Execution Flow & Deployment Matrix

### Step-by-Step Production Roadmap
1.  **Ingestion Validation**: System validates files in the localized runtime root directory (`/ingest`).
2.  **Asset Proxy Assembly**: Python calls native system FFmpeg paths to assemble tracking audio layers and downsampled visual tracks.
3.  **Cloud Analysis Loop**: Iteratively calls Gemini 3.7 Flash using specialized structured generation calls to guarantee JSON typing compliance.
4.  **Conforming Pipeline**: Generates clean XML sequences and outputs a confirmation report file.

---

## 7. Operational Safety and Budget Controls
*   **Token Threshold Monitoring**: A local helper check evaluates proxy files before transmission. If a visual track file exceeds the 1,000,000 input context threshold, it dynamically splits the asset payload into balanced sequence chunks.
*   **Structured Output Typing Enforcement**: Every Gemini payload call requires a defined strict JSON schema model mapping layout, eliminating runtime translation exceptions.
