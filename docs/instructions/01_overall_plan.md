# Goal

Put a tons of tables on Databricks workspace under multiple Genie rooms, each room has a domain, allowing some tables to be included in more than one room/domain, if that makes sense.

# Overall Plan
Please leverage databricks skills and MCP, this is a large project, please break it down into smaller tasks, for all the code and documentation and artifacts, please put under the @tables_to_genies folder, you can use subfolder to organize the code and documentation and artifacts.

## 1. Synthesize catalog.schema.tables for different domains
On the targetted databricks workspace, synthesize catalog.schema.tables for different domains, at least 20, covering World Cup 2026, NFL, NBA, NASA, GenAI, Nutritions, Iron Chef, Claim, provider, drug discovery, pharmaceuticals, semiconductor chips, international policy, japanese anime, rock bands in US history, world war II, Roman history,etc. 
 a. for each domain, synthesize at least 5 tables, each table with at least 5 columns, at least 100 rows of data
 b. to simulate real life scenario, mix some domains under a catalog or a schema, while keeping most domains under their own separate catalog.schema

## 2. Create and deploy a Databricks App
once these data are ready, create and deploy a Databricks App, which can let user:
1. a page to browse the workspace UC catalog and choose to include or exclude certain catalog.schema.tables, they can either operate on the tables level, or operate at the schema or catalog level for inclusion or exclusion
 
2. once the user makes the choice, with "next" button, another page should be shown to them, they should be able to see the list of tables that are included, and the app should provide a button to let them trigger the /etl/02_enrich_table_metadata.py for enrichment of the included tables. Clone the script, make adaption of this script so that can handle tables directly without the need of Genie space.json exports cause we dont have Genie yet. Once the enrichment job is complete, they should be able to see the enrichment results in a table displayed in the app.

3. If they click "next" button, another page should be shown to them, the app should provide a interface to let users trigger the graph algorithm to build a graph of the included tables, according to 1). table location (under what catalog.schema) 2). table summary level metadata. 3). column level metadata. Once the graph is built, they should be able to see the graph, freely zoom in and out, pan, and click on the nodes (representing the tables) to see the details of the tables and columns information.
    - Microsoft GraphRAG to build the graph.
    - Cytoscape.js for visualizing the graph in the app.

4. They can then click and/or box select a subset of the nodes, UI will highlight the selected nodes, and the app should provide a button to let them input the name of the Genie room to be created for the selected tables. They can repeat this process to create multiple Genie rooms. The sub-panel should show the name of the Genie room, which is expandable to show the tables and columns information.

5. Once they are done, they can click "create Genie rooms" button, the app should trigger databricks skills to create the Genie rooms, and the app should show the progress of the creation process. Once the creation is complete, the app should show the success message, and the user can click "view Genie rooms" button to view the created Genie rooms, each room should have a unique URL, which is clickable to open the room in a new tab.
    
