import langgraph

# Minimal LangGraph graph example
graph = langgraph.Graph()

# Add nodes
node1 = graph.add_node('node1')
node2 = graph.add_node('node2')

# Add edges
edge1 = graph.add_edge(node1, node2)

# Run the graph
graph.run()
