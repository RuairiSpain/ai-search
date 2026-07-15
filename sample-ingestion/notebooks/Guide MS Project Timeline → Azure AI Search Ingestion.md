# Extracting Microsoft Project Timeline Screenshots into Azure AI Search

## Overview

This document describes how to process a screenshot of a Microsoft Project timeline or Gantt chart and convert it into structured, searchable data in Azure AI Search.

## Problem

A screenshot of a project timeline is not a normal text document.

A timeline contains:

- Phase names
- Milestones
- Dates
- Durations
- Dependencies
- Visual bar positions
- Progress indicators

Traditional OCR can extract text, but it cannot understand timeline structure.

Example:

```text
Requirements
Design
Build
Test
Go Live
```

OCR alone loses:

- Start dates
- End dates
- Durations
- Relationships between phases

---

# Recommended Architecture

```text
MS Project Screenshot
        |
        v
Azure Blob Storage
        |
        v
Azure AI Document Intelligence
(Layout + OCR)
        |
        v
Custom Timeline Extraction Skill
        |
        +--> Phase Names
        +--> Milestones
        +--> Start Dates
        +--> End Dates
        +--> Dependencies
        |
        v
Metadata Enrichment
        |
        v
Azure OpenAI Embeddings
        |
        v
Azure AI Search
```

---

# Step 1 - OCR Extraction

Use Azure AI Document Intelligence Layout model.

Example:

```python
from azure.ai.documentintelligence import DocumentIntelligenceClient
```

Extract:

- Lines
- Words
- Tables
- Bounding boxes
- Coordinates

Example output:

```json
{
  "text": "Build Phase",
  "x": 324,
  "y": 214,
  "width": 88,
  "height": 19
}
```

---

# Step 2 - Extract Timeline Dates

Many project timelines contain a header row.

Example:

```text
Jan Feb Mar Apr May Jun
```

OCR extracts the text.

Store coordinates:

```json
[
  {
    "month": "Jan",
    "x": 100
  },
  {
    "month": "Feb",
    "x": 180
  },
  {
    "month": "Mar",
    "x": 260
  }
]
```

These x-coordinates become the date scale.

---

# Step 3 - Detect Timeline Bars

OCR does not identify bars.

Use OpenCV.

Example:

```python
import cv2

image = cv2.imread("timeline.png")
contours, _ = cv2.findContours(
    processed_image,
    cv2.RETR_EXTERNAL,
    cv2.CHAIN_APPROX_SIMPLE,
)
```

For every detected bar:

```json
{
  "start_x": 210,
  "end_x": 450
}
```

Using the date scale:

```json
{
  "phase": "Build",
  "startDate": "2026-03-01",
  "endDate": "2026-05-15"
}
```

---

# Step 4 - Create a Custom AI Search Skill

Custom Web API Skill inputs:

```json
{
  "imageUrl": "timeline.png",
  "ocrText": "...",
  "layout": "..."
}
```

Outputs:

```json
{
  "projectName": "CRM Modernisation",
  "phases": [
    {
      "name": "Design",
      "startDate": "2026-02-01",
      "endDate": "2026-03-15"
    },
    {
      "name": "Build",
      "startDate": "2026-03-16",
      "endDate": "2026-06-01"
    }
  ]
}
```

---

# Step 5 - Generate Search Metadata

Store structured information.

```json
{
  "projectName": "CRM Modernisation",
  "phaseName": "Build",
  "startDate": "2026-03-16",
  "endDate": "2026-06-01",
  "durationDays": 77,
  "phaseType": "Delivery"
}
```

This supports:

- Filters
- Sorting
- Facets
- Exact date matching

---

# Step 6 - Generate Searchable Narrative Content

Create AI-readable text.

Example:

```text
Project CRM Modernisation.
Phase Build starts 16 March 2026.
Phase Build ends 1 June 2026.
Duration 77 days.
```

Store in:

```json
{
  "content": "Project CRM Modernisation..."
}
```

Generate embeddings for:

```json
{
  "vector": [ ... ]
}
```

---

# Recommended AI Search Schema

```json
{
  "id": "phase-001",
  "projectName": "CRM Modernisation",
  "phaseName": "Build",
  "startDate": "2026-03-16",
  "endDate": "2026-06-01",
  "durationDays": 77,
  "milestones": [],
  "dependencies": [],
  "content": "...",
  "sourceFile": "timeline.png",
  "vector": []
}
```

---

# Query Examples

## Keyword Search

```python
search_text = "Build"
```

## Date Search

```python
filter = "startDate ge 2026-03-01T00:00:00Z"
```

## Semantic Search

```python
search_text = "Which projects are currently in delivery?"
```

## Hybrid Search

```python
search_text = query
vector_queries = [vector_query]
query_type = "semantic"
```

---

# Security Trimming

Add metadata:

```json
{
  "group_ids": ["programme-office"],
  "classification": "Internal"
}
```

Filter before retrieval:

```python
filter = (
    "group_ids/any(g: search.in(g,'programme-office'))"
)
```

---

# Best Practice

For Microsoft Project screenshots:

1. OCR is necessary but insufficient.
2. Use Document Intelligence Layout extraction.
3. Use OpenCV or custom image analysis for timeline bars.
4. Convert bar locations into dates.
5. Create structured phase metadata.
6. Generate narrative summaries.
7. Embed summaries.
8. Index both metadata and vectors.
9. Apply security trimming.
10. Use Hybrid + Semantic Search for retrieval.

This approach transforms a simple timeline screenshot into a rich project knowledge source suitable for Azure AI Search, RAG solutions, AI agents and Azure AI Foundry workloads.
