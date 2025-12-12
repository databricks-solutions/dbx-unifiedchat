# Architecture Diagram Files - Quick Reference

## Generated Files

I've created **7 different formats** of the architecture diagram for your multi-agent system:

### 1. ✅ **architecture_diagram.png** (438 KB)
- **Use for**: Presentations, documentation, quick viewing
- **Format**: High-resolution PNG image (4000x3000px)
- **Editable**: No (static image)
- **Best for**: PowerPoint, Word, Google Docs, Confluence

### 2. ✅ **architecture_diagram.svg** (101 KB)
- **Use for**: Web pages, scalable graphics
- **Format**: Scalable Vector Graphics
- **Editable**: Yes (can edit in Inkscape, Adobe Illustrator)
- **Best for**: Websites, high-quality prints, zooming without quality loss

### 3. ✅ **architecture_diagram.pdf** (108 KB)
- **Use for**: Printing, sharing, archiving
- **Format**: PDF document
- **Editable**: Yes (can edit in Adobe Illustrator or convert back)
- **Best for**: Professional documents, printing, email attachments

### 4. ✅ **architecture_diagram.mmd** (4.8 KB)
- **Use for**: Source file for Mermaid diagrams
- **Format**: Mermaid Markdown syntax
- **Editable**: Yes (text-based, highly editable)
- **Best for**: Version control, GitHub README, Markdown viewers
- **Online Editor**: https://mermaid.live

### 5. ✅ **architecture_diagram.puml** (5.6 KB)
- **Use for**: Source file for PlantUML diagrams
- **Format**: PlantUML syntax
- **Editable**: Yes (text-based)
- **Best for**: Technical documentation, UML tools
- **Online Editor**: https://www.plantuml.com/plantuml

### 6. ✅ **architecture_nodes_edges.csv** 
- **Use for**: Importing into Lucid Chart, Visio, other tools
- **Format**: CSV with nodes and edges
- **Editable**: Yes (spreadsheet format)
- **Best for**: Data-driven diagram tools, Lucid Chart import

### 7. ✅ **ARCHITECTURE_DIAGRAM.md**
- **Use for**: Complete documentation with embedded diagram
- **Format**: Markdown with explanations
- **Editable**: Yes
- **Best for**: GitHub, technical documentation, team sharing

## How to Import into Lucid Chart

### Method 1: Using CSV Import
1. Open Lucid Chart
2. Go to **File → Import Data → CSV**
3. Select `architecture_nodes_edges.csv`
4. Map columns:
   - **Source** → From
   - **Target** → To  
   - **Label** → Connection Label
   - **Category** → Group/Layer
5. Click **Generate**
6. Apply colors based on the "Color" column in the CSV

### Method 2: Using SVG/PDF Import
1. Open Lucid Chart
2. Go to **File → Import**
3. Select `architecture_diagram.svg` or `architecture_diagram.pdf`
4. The diagram will be imported as an image
5. You can then trace over it or use it as a reference

### Method 3: Manual Recreation (Best for Editing)
1. Open the `ARCHITECTURE_DIAGRAM.md` file
2. Follow the component descriptions
3. Use the CSV file as a reference for connections
4. Apply the color scheme from the "Color Legend" section

## How to Edit the Diagrams

### To Edit and Re-generate PNG/PDF/SVG:

1. **Edit the Mermaid source file**:
```bash
# Open in your favorite text editor
code architecture_diagram.mmd
# or
nano architecture_diagram.mmd
```

2. **Re-generate images**:
```bash
cd /Users/yang.yang/CursorProjects/KUMC_POC_hlsfieldtemp

# Generate PNG
mmdc -i architecture_diagram.mmd -o architecture_diagram.png -w 4000 -H 3000 -b transparent

# Generate SVG
mmdc -i architecture_diagram.mmd -o architecture_diagram.svg -b transparent

# Generate PDF
mmdc -i architecture_diagram.mmd -o architecture_diagram.pdf -b transparent
```

### To Edit Using Online Tools:

#### Mermaid Live Editor
1. Go to https://mermaid.live
2. Copy contents of `architecture_diagram.mmd`
3. Paste into the editor
4. Edit visually or in code
5. Export as PNG/SVG/PDF

#### PlantUML Online
1. Go to https://www.plantuml.com/plantuml
2. Copy contents of `architecture_diagram.puml`
3. Paste into the editor
4. Download as PNG/SVG/PDF

## Color Scheme Reference

Use these colors when recreating in Lucid Chart or other tools:

| Component Type | Color Code | Usage |
|---------------|------------|-------|
| **Agents** | `#4A90E2` (Blue) | Super Agent, Thinking Agent, Genie Agents, SQL Agents |
| **Data Stores** | `#50C878` (Green) | Vector Search Index, Delta Tables |
| **Processes** | `#F5A623` (Orange) | Search, Synthesis, Merge operations |
| **Decisions** | `#E94B3C` (Red) | Clarity check, routing decisions |
| **Pipelines** | `#9B59B6` (Purple) | Table metadata, VS index, exports |
| **Future** | `#BDC3C7` (Gray) | Caching system components |
| **Integration** | `#FFA500` (Orange) | MLflow |

## Architecture Overview

The diagram shows:

### Main Flow (Top Section)
- User query enters through Super Agent
- Thinking & Planning Agent breaks down the query
- Decision points route to appropriate execution paths:
  - **Single Agent Path**: One Genie Agent handles everything
  - **Multiple Agents + Join (Fast)**: Quick SQL synthesis from metadata
  - **Multiple Agents + Join (Slow)**: Comprehensive async processing
  - **Multiple Agents + Verbal Merge**: Qualitative integration

### Supporting Pipelines (Bottom Section)
- **Pipeline 3**: Genie Space Export (foundation)
- **Pipeline 1**: Table Metadata Update (enrichment)
- **Pipeline 2**: Vector Search Index Generation (indexing)

### Key Components
- **Vector Search Index**: Stores enriched metadata for agent discovery
- **Delta Tables**: Backend data storage
- **MLflow**: Logging and deployment for all agents
- **Caching System** (Future): Performance optimization

## Quick Start

### For Viewing:
- Open `architecture_diagram.png` in any image viewer
- Open `architecture_diagram.pdf` in any PDF reader

### For Editing:
1. **In Lucid Chart**: Import `architecture_nodes_edges.csv`
2. **In Draw.io**: Import `architecture_diagram.svg` or create from scratch
3. **In Code**: Edit `architecture_diagram.mmd` and regenerate

### For Documentation:
- Share `ARCHITECTURE_DIAGRAM.md` - it has everything!
- Include `architecture_diagram.png` in presentations
- Attach `architecture_diagram.pdf` to emails

## System Requirements

To regenerate images locally:
- **Node.js** and **npm** (for Mermaid CLI)
- **@mermaid-js/mermaid-cli** package (already installed)

```bash
# To install (if needed on another machine)
npm install -g @mermaid-js/mermaid-cli
```

## Tips

1. **For presentations**: Use the PNG file
2. **For web**: Use the SVG file (smaller and scalable)
3. **For printing**: Use the PDF file
4. **For collaboration**: Share the Mermaid or PlantUML source files
5. **For import**: Use the CSV file with Lucid Chart or Visio
6. **For documentation**: Use the Markdown file

## Need Help?

- **Mermaid Syntax**: https://mermaid.js.org/intro/
- **PlantUML Guide**: https://plantuml.com/guide
- **Lucid Chart Import**: https://help.lucidchart.com/hc/en-us/articles/207299756-Import-Data
- **Draw.io**: https://www.diagrams.net/

---

*Generated on: December 8, 2025*  
*Project: KUMC POC - Multi-Agent System*

