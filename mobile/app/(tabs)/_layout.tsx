/**
 * Tab layout — 4 tabs: Map, Wallet, Activity, Profile.
 */

import { Tabs } from "expo-router";
import { Text } from "react-native";

export default function TabsLayout(): React.JSX.Element {
  return (
    <Tabs
      screenOptions={{
        headerStyle: { backgroundColor: "#0f172a" },
        headerTintColor: "#fff",
        tabBarActiveTintColor: "#0f172a",
        tabBarInactiveTintColor: "#94a3b8",
        tabBarStyle: { backgroundColor: "#fff" },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Map",
          tabBarIcon: (props) => (
            <Text style={{ color: props.color, fontSize: props.size }}>{"🗺️"}</Text>
          ),
        }}
      />
      <Tabs.Screen
        name="wallet"
        options={{
          title: "Wallet",
          tabBarIcon: (props) => (
            <Text style={{ color: props.color, fontSize: props.size }}>{"💳"}</Text>
          ),
        }}
      />
      <Tabs.Screen
        name="activity"
        options={{
          title: "Activity",
          tabBarIcon: (props) => (
            <Text style={{ color: props.color, fontSize: props.size }}>{"⚡"}</Text>
          ),
        }}
      />
      <Tabs.Screen
        name="profile"
        options={{
          title: "Profile",
          tabBarIcon: (props) => (
            <Text style={{ color: props.color, fontSize: props.size }}>{"👤"}</Text>
          ),
        }}
      />
    </Tabs>
  );
}
