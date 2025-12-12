# Architecture Diagrams - Quick Start Guide

## 📦 What You Got

I've generated **11 files** for your multi-agent system architecture:

### Main Diagrams (Detailed)
- ✅ `architecture_diagram.png` - High-res image for presentations
- ✅ `architecture_diagram.svg` - Scalable vector for web/editing
- ✅ `architecture_diagram.pdf` - Print-ready PDF
- ✅ `architecture_diagram.mmd` - Mermaid source (editable)
- ✅ `architecture_diagram.puml` - PlantUML source (editable)

### Simplified Diagrams (Overview)
- ✅ `architecture_diagram_simple.png` - Simplified image
- ✅ `architecture_diagram_simple.svg` - Simplified vector
- ✅ `architecture_diagram_simple.pdf` - Simplified PDF
- ✅ `architecture_diagram_simple.mmd` - Simplified source

### Import/Documentation
- ✅ `architecture_nodes_edges.csv` - For Lucid Chart/Visio import
- ✅ `ARCHITECTURE_DIAGRAM.md` - Full documentation
- ✅ `DIAGRAM_FILES_SUMMARY.md` - Detailed guide (this file)

## 🚀 Quick Actions

### "I want to import into Lucid Chart"
→ Use: `architecture_nodes_edges.csv`
1. Lucid Chart → File → Import Data → CSV
2. Select the CSV file
3. Map: Source → From, Target → To, Label → Connection Label
4. Done!

### "I want to show in a presentation"
→ Use: `architecture_diagram.png` or `architecture_diagram_simple.png`
- Drag and drop into PowerPoint, Keynote, or Google Slides

### "I want to include in documentation"
→ Use: `ARCHITECTURE_DIAGRAM.md`
- Copy the Mermaid code block
- Paste into GitHub, Confluence, or Notion

### "I want to edit the diagram"
→ Use: `architecture_diagram.mmd`
1. Go to https://mermaid.live
2. Paste the content from the `.mmd` file
3. Edit visually
4. Export as PNG/SVG/PDF

### "I want to print it"
→ Use: `architecture_diagram.pdf`
- Open and print directly

## 🎨 Two Versions Available

### Detailed Version (`architecture_diagram.*`)
- Shows all components, decision points, and pipelines
- 50+ nodes with full execution paths
- Best for: Technical documentation, team discussions

### Simplified Version (`architecture_diagram_simple.*`)
- High-level overview with key components only
- ~15 nodes with main flow
- Best for: Executive presentations, quick understanding

## 📋 Component Overview

### Core Agents
- **Super Agent** - Main orchestrator
- **Thinking & Planning Agent** - Query analysis
- **Genie Agents** - Domain-specific agents (Patients, Medications, etc.)
- **SQL Synthesis Agent** - SQL query assembly
- **SQL Execution Agent** - Query execution

### Execution Paths
1. **Single Agent** - One Genie Agent handles query
2. **Fast Route** - Quick SQL synthesis from metadata
3. **Slow Route** - Comprehensive async processing
4. **Verbal Merge** - Qualitative integration without joins

### Data Components
- **Vector Search Index** - Enriched metadata store
- **Delta Tables** - Backend data storage

### Pipelines (Build Order)
1. Genie Space Export → space.json
2. Table Metadata Update → enriched metadata
3. Vector Search Index → searchable index

## 🔧 Editing Commands

```bash
# Navigate to project
cd /Users/yang.yang/CursorProjects/KUMC_POC_hlsfieldtemp

# Edit the source
nano architecture_diagram.mmd

# Regenerate all formats
mmdc -i architecture_diagram.mmd -o architecture_diagram.png -w 4000 -H 3000 -b transparent
mmdc -i architecture_diagram.mmd -o architecture_diagram.svg -b transparent
mmdc -i architecture_diagram.mmd -o architecture_diagram.pdf -b transparent
```

## 🎯 Choose Your Format

| Need | Use This File | Why |
|------|---------------|-----|
| Present to executives | `architecture_diagram_simple.png` | Clean, high-level view |
| Technical review | `architecture_diagram.png` | Full details |
| Import to Lucid Chart | `architecture_nodes_edges.csv` | Direct import |
| Edit online | `architecture_diagram.mmd` | Mermaid Live Editor |
| Print on paper | `architecture_diagram.pdf` | Print-optimized |
| Website/blog | `architecture_diagram.svg` | Scalable, small file |
| Share with team | `ARCHITECTURE_DIAGRAM.md` | Everything in one doc |

## 🌈 Color Meaning

- 🔵 Blue - Agents (Super, Thinking, Genie, SQL)
- 🟢 Green - Data (Vector Index, Delta Tables)
- 🟠 Orange - Processes (Search, Synthesis, Merge)
- 🔴 Red - Decisions (Route choices)
- 🟣 Purple - Pipelines (Background processes)

## 💡 Pro Tips

1. **For stakeholder meetings**: Start with simple version, drill into detailed version for questions
2. **For documentation**: Embed PNG in Word/Confluence, link to MD file for details
3. **For collaboration**: Share Mermaid `.mmd` file - team can edit in any text editor
4. **For archiving**: Keep all files - different tools prefer different formats

## 📚 Example Use Case

### Query: "How many patients older than 50 are on Voltaren?"

**Flow in the diagram:**
1. User → Super Agent
2. Super Agent → Thinking Agent
3. Thinking Agent → Breaks down:
   - Sub-query 1: Patients > 50 years
   - Sub-query 2: Patients on Voltaren
4. Vector Search → Identifies 2 Genie Agents needed
5. Decision → Multiple agents + Join required
6. Fast Route (parallel):
   - SQL Synthesis → Creates joined query
   - SQL Execution → Runs on Delta Tables
   - Returns count
7. Slow Route (parallel):
   - Patients Agent → Gets patients > 50
   - Medications Agent → Gets patients on Voltaren
   - SQL Synthesis → Joins on patient_id
   - Returns comprehensive result
8. Super Agent → Returns both results to User

## 🔗 Useful Links

- **Mermaid Live Editor**: https://mermaid.live (paste `.mmd` content)
- **PlantUML Online**: https://plantuml.com/plantuml (paste `.puml` content)
- **Lucid Chart Import Guide**: https://help.lucidchart.com/hc/en-us/articles/207299756
- **Mermaid Documentation**: https://mermaid.js.org
- **Original Requirements**: `Instructions/01_overall.md`

## ✅ Checklist for Lucid Chart Import

- [ ] Open `architecture_nodes_edges.csv` in Excel/Numbers to verify
- [ ] Open Lucid Chart
- [ ] Go to File → Import Data → CSV
- [ ] Upload `architecture_nodes_edges.csv`
- [ ] Map columns: Source→From, Target→To, Label→Connection Label
- [ ] Set shape colors based on Category column
- [ ] Review and adjust layout
- [ ] Save your Lucid Chart diagram

## 🎓 Understanding the Architecture

### Three-Layer Design

**Layer 1: User Interface**
- User interacts with Super Agent
- Gets clarification if needed
- Receives final answers

**Layer 2: Intelligence**
- Thinking Agent analyzes queries
- Vector Search finds relevant agents
- Decision routing to optimal path

**Layer 3: Execution**
- Genie Agents handle domain queries
- SQL Agents synthesize and execute
- Delta Tables provide data

### Data Flow

1. **Ingest**: Genie Spaces export → space.json
2. **Enrich**: Table metadata → value samples + dictionaries
3. **Index**: Vector Search → searchable metadata
4. **Query**: User question → parsed → routed → executed
5. **Return**: Results → integrated → delivered

## 🚨 Important Notes

- All agents log to **MLflow** for monitoring
- Build pipelines in order: **3 → 1 → 2** (as specified in requirements)
- Fast route provides quick response, slow route ensures accuracy
- Future: Three caching layers planned (full-text, SQL, semantic)

---

**Need More Details?** 
→ Open `ARCHITECTURE_DIAGRAM.md` or `DIAGRAM_FILES_SUMMARY.md`

**Ready to Build?**
→ Check `Instructions/01_overall.md` for implementation requirements

**Questions?**
→ All source files are text-based and fully documented

